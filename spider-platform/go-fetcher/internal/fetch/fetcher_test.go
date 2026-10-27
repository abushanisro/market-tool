package fetch

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func policy() Policy {
	return Policy{UserAgent: "TestBot/1.0 (+https://example.test)", Concurrency: 2, MaxRetries: 3,
		BackoffBase: time.Millisecond, BackoffCap: 20 * time.Millisecond, RequestTimeout: 5 * time.Second}
}

func TestConditionalGetAndUnchanged(t *testing.T) {
	var gotINM, gotIMS string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotINM, gotIMS = r.Header.Get("If-None-Match"), r.Header.Get("If-Modified-Since")
		if gotINM == `"v1"` {
			w.WriteHeader(http.StatusNotModified)
			return
		}
		w.Header().Set("ETag", `"v1"`)
		_, _ = w.Write([]byte(`{"a":1}`))
	}))
	defer srv.Close()
	f := New(policy(), nil)

	first := f.Fetch(context.Background(), srv.URL, Checkpoint{}, false)
	if first.Err != nil || first.Status != 200 || first.NotModified || first.Unchanged || first.Hash == "" {
		t.Fatalf("first fetch: %+v", first)
	}
	if first.Header.Get("ETag") != `"v1"` {
		t.Fatalf("etag not exposed: %v", first.Header)
	}

	second := f.Fetch(context.Background(), srv.URL, Checkpoint{ETag: `"v1"`, LastModified: "Tue, 27 Oct 2026 10:00:00 GMT"}, false)
	if !second.NotModified || second.Body != nil || gotINM != `"v1"` || gotIMS == "" {
		t.Fatalf("expected 304 with validators sent, got %+v inm=%q ims=%q", second, gotINM, gotIMS)
	}

	// force ignores validators → full body again
	forced := f.Fetch(context.Background(), srv.URL, Checkpoint{ETag: `"v1"`}, true)
	if forced.NotModified || gotINM != "" || forced.Status != 200 {
		t.Fatalf("force should skip conditional headers: %+v inm=%q", forced, gotINM)
	}

	// same content hash as checkpoint (server without validators) → Unchanged, but body still delivered
	again := f.Fetch(context.Background(), srv.URL, Checkpoint{ContentHash: first.Hash}, true)
	if again.Unchanged {
		t.Fatal("force must disable unchanged detection")
	}
	again = f.Fetch(context.Background(), srv.URL, Checkpoint{ContentHash: first.Hash}, false)
	if !again.Unchanged {
		t.Fatalf("identical hash should be flagged unchanged: %+v", again)
	}
}

func TestRetryHonoursRetryAfterAndRecovers(t *testing.T) {
	var n atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if n.Add(1) <= 2 {
			w.Header().Set("Retry-After", "0")
			w.WriteHeader(http.StatusTooManyRequests)
			return
		}
		_, _ = w.Write([]byte("ok"))
	}))
	defer srv.Close()
	f := New(policy(), nil)
	var retries int
	f.OnRetry = func(string, int, time.Duration, string) { retries++ }
	res := f.Fetch(context.Background(), srv.URL, Checkpoint{}, false)
	if res.Err != nil || res.Status != 200 || res.Retries != 2 || retries != 2 || string(res.Body) != "ok" {
		t.Fatalf("got %+v retries=%d", res, retries)
	}
}

func TestRetriesAreBounded(t *testing.T) {
	var n atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n.Add(1)
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer srv.Close()
	p := policy()
	p.MaxRetries = 2
	res := New(p, nil).Fetch(context.Background(), srv.URL, Checkpoint{}, false)
	if res.Status != 503 || n.Load() != 3 || res.Retries != 2 {
		t.Fatalf("want 503 after 3 attempts, got status=%d attempts=%d retries=%d", res.Status, n.Load(), res.Retries)
	}
}

func TestClientErrorsAreNotRetried(t *testing.T) {
	var n atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n.Add(1)
		http.NotFound(w, r)
	}))
	defer srv.Close()
	res := New(policy(), nil).Fetch(context.Background(), srv.URL, Checkpoint{}, false)
	if res.Status != 404 || n.Load() != 1 || res.Err != nil {
		t.Fatalf("404 must be returned once, got %+v attempts=%d", res, n.Load())
	}
}

func TestTransportErrorIsRetriedThenReported(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	url := srv.URL
	srv.Close() // connection refused
	p := policy()
	p.MaxRetries = 1
	res := New(p, nil).Fetch(context.Background(), url, Checkpoint{}, false)
	if res.Err == nil || res.Status != 0 || res.Retries != 1 {
		t.Fatalf("got %+v", res)
	}
}

func TestPerHostPacing(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	defer srv.Close()
	p := policy()
	p.Delay = 60 * time.Millisecond
	f := New(p, nil)
	start := time.Now()
	for i := 0; i < 4; i++ {
		f.Fetch(context.Background(), srv.URL, Checkpoint{}, false)
	}
	// three gaps, each at least 0.5×delay (jitter lower bound)
	if el := time.Since(start); el < 85*time.Millisecond {
		t.Fatalf("requests to one host were not spaced: %v", el)
	}
}

func TestPerHostConcurrencyCap(t *testing.T) {
	var cur, peak atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c := cur.Add(1)
		for {
			p := peak.Load()
			if c <= p || peak.CompareAndSwap(p, c) {
				break
			}
		}
		time.Sleep(30 * time.Millisecond)
		cur.Add(-1)
	}))
	defer srv.Close()
	f := New(policy(), nil) // Concurrency: 2, no delay
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); f.Fetch(context.Background(), srv.URL, Checkpoint{}, false) }()
	}
	wg.Wait()
	if peak.Load() > 2 || peak.Load() < 2 {
		t.Fatalf("peak concurrency %d, want exactly the cap (2)", peak.Load())
	}
}

func TestRobotsTxt(t *testing.T) {
	var robotsHits atomic.Int32
	status := 200
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/robots.txt" {
			robotsHits.Add(1)
			w.WriteHeader(status)
			if status == 200 {
				_, _ = w.Write([]byte("User-agent: TestBot\nDisallow: /private\n\nUser-agent: *\nDisallow: /\n"))
			}
			return
		}
		_, _ = w.Write([]byte("page"))
	}))
	defer srv.Close()

	p := policy()
	p.RespectRobots = true
	f := New(p, nil)
	if res := f.Fetch(context.Background(), srv.URL+"/public?x=1", Checkpoint{}, false); res.Err != nil || res.Status != 200 {
		t.Fatalf("public should be allowed for our agent group: %+v", res)
	}
	if res := f.Fetch(context.Background(), srv.URL+"/private/x", Checkpoint{}, false); !errors.Is(res.Err, ErrRobots) {
		t.Fatalf("private must be blocked, got %+v", res)
	}
	if robotsHits.Load() != 1 {
		t.Fatalf("robots.txt must be fetched once per host, got %d", robotsHits.Load())
	}

	status = 404 // no robots.txt → everything allowed
	if res := New(p, nil).Fetch(context.Background(), srv.URL+"/private", Checkpoint{}, false); res.Err != nil {
		t.Fatalf("404 robots means allow: %+v", res)
	}
	status = 503 // robots unavailable → refuse
	res := New(p, nil).Fetch(context.Background(), srv.URL+"/public", Checkpoint{}, false)
	if res.Status != 0 || !errors.Is(res.Err, ErrRobots) {
		t.Fatalf("5xx robots must refuse the fetch, got %+v", res)
	}
}

func TestRobotsIgnoredWhenDisabled(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/robots.txt" {
			t.Error("robots.txt must not be requested when respect_robots is off")
		}
	}))
	defer srv.Close()
	if res := New(policy(), nil).Fetch(context.Background(), srv.URL+"/x", Checkpoint{}, false); res.Err != nil {
		t.Fatal(res.Err)
	}
}

func TestBodyLimit(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(strings.Repeat("x", 2048)))
	}))
	defer srv.Close()
	p := policy()
	p.MaxBody = 1024
	res := New(p, nil).Fetch(context.Background(), srv.URL, Checkpoint{}, false)
	if !errors.Is(res.Err, ErrBodyTooLarge) || res.Retries != 0 {
		t.Fatalf("got %+v", res)
	}
}

func TestCancelDuringBackoffReturnsPromptly(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Retry-After", "60")
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer srv.Close()
	p := policy()
	p.BackoffCap = time.Minute
	ctx, cancel := context.WithTimeout(context.Background(), 150*time.Millisecond)
	defer cancel()
	start := time.Now()
	res := New(p, nil).Fetch(ctx, srv.URL, Checkpoint{}, false)
	if !errors.Is(res.Err, context.DeadlineExceeded) || time.Since(start) > 2*time.Second {
		t.Fatalf("cancel not honoured: %+v after %v", res, time.Since(start))
	}
}

func TestBackoffGrowsAndIsCapped(t *testing.T) {
	f := New(Policy{BackoffBase: 100 * time.Millisecond, BackoffCap: 500 * time.Millisecond}, nil)
	for attempt, want := range []time.Duration{100, 200, 400, 500, 500} {
		hi := want * time.Millisecond
		for i := 0; i < 50; i++ {
			d := f.backoff(attempt, nil)
			if d > hi || d < hi/2 {
				t.Fatalf("attempt %d: %v outside [%v, %v]", attempt, d, hi/2, hi)
			}
		}
	}
	h := http.Header{"Retry-After": {"3"}}
	if d := f.backoff(0, h); d != 500*time.Millisecond {
		t.Fatalf("Retry-After must be capped by BackoffCap, got %v", d)
	}
}

func TestReplayTransport(t *testing.T) {
	rep, err := NewReplay("../../../tests/fixtures/replay")
	if err != nil {
		t.Fatal(err)
	}
	p := policy()
	p.RespectRobots = true // robots.txt is a synthetic 404 in replay → allowed
	f := New(p, rep)
	res := f.Fetch(context.Background(), "https://api.frankfurter.dev/v1/latest?base=USD", Checkpoint{}, false)
	if res.Err != nil || res.Status != 200 || res.Replay["captured_at"] == nil || len(res.Body) == 0 {
		t.Fatalf("replay hit: %+v", res)
	}
	if res.Header.Get("X-Emithran-Replay") != "" {
		t.Fatal("internal replay header must be stripped")
	}
	miss := f.Fetch(context.Background(), "https://api.frankfurter.dev/v1/nope", Checkpoint{}, false)
	if miss.Status != 404 || miss.Replay != nil {
		t.Fatalf("unrecorded URL must 404 without touching the network: %+v", miss)
	}
}

func TestInvalidURL(t *testing.T) {
	for _, u := range []string{"ftp://x/y", "not a url", "http://"} {
		if res := New(policy(), nil).Fetch(context.Background(), u, Checkpoint{}, false); res.Err == nil {
			t.Errorf("%q should be rejected", u)
		}
	}
}

func TestRateLimitWithoutRetryAfterBacksOffHardAndPausesTheHost(t *testing.T) {
	var n atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if n.Add(1) <= 2 {
			w.WriteHeader(http.StatusTooManyRequests) // no Retry-After header
			return
		}
		_, _ = w.Write([]byte("ok"))
	}))
	defer srv.Close()
	p := policy()
	p.BackoffBase, p.BackoffCap, p.RateLimitFloor = time.Millisecond, 400*time.Millisecond, 60*time.Millisecond
	f := New(p, nil)
	start := time.Now()
	res := f.Fetch(context.Background(), srv.URL, Checkpoint{}, false)
	// attempt 0 waits >= 60ms, attempt 1 waits >= 120ms: far above the 1ms generic backoff
	if res.Status != 200 || res.Retries != 2 || time.Since(start) < 170*time.Millisecond {
		t.Fatalf("429 must back off hard: status=%d retries=%d took %v", res.Status, res.Retries, time.Since(start))
	}
}
