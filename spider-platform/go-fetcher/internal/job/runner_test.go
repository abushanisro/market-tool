package job

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"emithran.com/fetcher/internal/api"
)

// fakeCP stands in for the Python control plane.
type fakeCP struct {
	t        *testing.T
	srv      *httptest.Server
	plan     api.Plan
	planCode int // non-zero → plan endpoint fails with this status
	pagesFn  func(pages []api.Page) (int, api.PagesResponse)
	cancelAt atomic.Bool

	mu       sync.Mutex
	pages    []api.Page
	finished []map[string]any
	beats    int
}

func newFakeCP(t *testing.T, plan api.Plan) *fakeCP {
	cp := &fakeCP{t: t, plan: plan}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/fetcher/jobs/1/plan", func(w http.ResponseWriter, r *http.Request) {
		if cp.planCode != 0 {
			http.Error(w, `{"detail":"nope"}`, cp.planCode)
			return
		}
		_ = json.NewEncoder(w).Encode(cp.plan)
	})
	mux.HandleFunc("POST /api/fetcher/jobs/1/pages", func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Pages []api.Page `json:"pages"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		code, resp := 200, api.PagesResponse{Follow: []api.Item{}}
		if cp.pagesFn != nil {
			code, resp = cp.pagesFn(body.Pages)
		}
		if code != 200 {
			http.Error(w, `{"detail":"rejected"}`, code)
			return
		}
		cp.mu.Lock()
		cp.pages = append(cp.pages, body.Pages...)
		cp.mu.Unlock()
		_ = json.NewEncoder(w).Encode(resp)
	})
	mux.HandleFunc("POST /api/fetcher/jobs/1/heartbeat", func(w http.ResponseWriter, r *http.Request) {
		cp.mu.Lock()
		cp.beats++
		cp.mu.Unlock()
		var hb map[string]any
		_ = json.NewDecoder(r.Body).Decode(&hb)
		if _, isList := hb["logs"].([]any); !isList {
			t.Errorf("heartbeat logs must be a JSON array, got %T (%v)", hb["logs"], hb["logs"])
		}
		_, _ = io.Copy(io.Discard, r.Body)
		_ = json.NewEncoder(w).Encode(map[string]bool{"cancel": cp.cancelAt.Load()})
	})
	mux.HandleFunc("POST /api/fetcher/jobs/1/finish", func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		cp.mu.Lock()
		cp.finished = append(cp.finished, body)
		cp.mu.Unlock()
		_ = json.NewEncoder(w).Encode(map[string]string{"status": "succeeded"})
	})
	cp.srv = httptest.NewServer(mux)
	t.Cleanup(cp.srv.Close)
	return cp
}

func (cp *fakeCP) opts() Options {
	c := api.New(cp.srv.URL, "tok")
	c.Backoff = time.Millisecond
	return Options{JobID: 1, API: c, HeartbeatEvery: 20 * time.Millisecond, FlushEvery: 10 * time.Millisecond,
		BackoffBase: time.Millisecond, BackoffCap: 5 * time.Millisecond}
}

func (cp *fakeCP) reason() string {
	cp.mu.Lock()
	defer cp.mu.Unlock()
	if len(cp.finished) != 1 {
		cp.t.Fatalf("expected exactly one finish call, got %d", len(cp.finished))
	}
	return cp.finished[0]["reason"].(string)
}

func basePlan(urls ...string) api.Plan {
	p := api.Plan{JobID: 1, Source: "t", Checkpoints: map[string]api.Checkpoint{},
		Policy: api.Policy{UserAgent: "T/1", Concurrency: 2, MaxPages: 50, TimeoutSeconds: 30, MaxRetries: 2}}
	for _, u := range urls {
		p.StartURLs = append(p.StartURLs, api.Item{URL: u, Page: 1})
	}
	return p
}

func TestRunFetchesFollowsDedupesAndFinishes(t *testing.T) {
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("ETag", `"e`+r.URL.Path+`"`)
		fmt.Fprintf(w, `{"path":%q}`, r.URL.Path)
	}))
	defer origin.Close()

	cp := newFakeCP(t, basePlan(origin.URL+"/p1"))
	cp.pagesFn = func(pages []api.Page) (int, api.PagesResponse) {
		var follow []api.Item
		for _, p := range pages {
			if strings.HasSuffix(p.URL, "/p1") { // pagination: two new pages + one duplicate of the start URL
				follow = append(follow,
					api.Item{URL: origin.URL + "/p2", Page: 2, Depth: 0},
					api.Item{URL: origin.URL + "/p3", Page: 3, Depth: 0},
					api.Item{URL: origin.URL + "/p1", Page: 1})
			}
		}
		return 200, api.PagesResponse{Follow: follow}
	}
	status, err := Run(context.Background(), cp.opts())
	if err != nil || status != "succeeded" {
		t.Fatalf("status=%q err=%v", status, err)
	}
	if got := cp.reason(); got != ReasonFinished {
		t.Fatalf("reason %q", got)
	}
	if len(cp.pages) != 3 {
		t.Fatalf("want 3 pages (start + 2 follows, duplicate dropped), got %d", len(cp.pages))
	}
	for _, p := range cp.pages {
		if p.Status != 200 || len(p.Body) == 0 || p.ContentHash == "" || p.ETag == "" || p.FetchedAt == nil || p.ContentType != "application/json" {
			t.Fatalf("incomplete page payload: %+v", p)
		}
	}
	fin := cp.finished[0]["stats"].(map[string]any)
	if fin["pages_fetched"].(float64) != 3 || fin["urls_duplicate"].(float64) != 1 || fin["urls_discovered"].(float64) != 3 {
		t.Fatalf("stats: %v", fin)
	}
	if cp.beats == 0 {
		t.Fatal("no heartbeat sent")
	}
}

func TestNotModifiedSkipsBodyAndChangedHashSendsIt(t *testing.T) {
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/etag" && r.Header.Get("If-None-Match") == `"v1"` {
			w.WriteHeader(http.StatusNotModified)
			return
		}
		_, _ = w.Write([]byte("same body"))
	}))
	defer origin.Close()
	plan := basePlan(origin.URL+"/etag", origin.URL+"/same")
	// sha256("same body")
	plan.Checkpoints[origin.URL+"/etag"] = api.Checkpoint{ETag: `"v1"`}
	plan.Checkpoints[origin.URL+"/same"] = api.Checkpoint{ContentHash: "c5ded0a6a0af1ce9c7bc9c0bba1a8f1d4a8d4a9f2c1c3ee2dd9f1d2d2d1c0f0e"}
	cp := newFakeCP(t, plan)
	if _, err := Run(context.Background(), cp.opts()); err != nil {
		t.Fatal(err)
	}
	byURL := map[string]api.Page{}
	for _, p := range cp.pages {
		byURL[p.URL] = p
	}
	nm := byURL[origin.URL+"/etag"]
	if !nm.NotModified || nm.Status != 304 || len(nm.Body) != 0 {
		t.Fatalf("304 page: %+v", nm)
	}
	// the hard-coded hash above is deliberately wrong → body must be sent as changed
	if same := byURL[origin.URL+"/same"]; same.Unchanged || string(same.Body) != "same body" {
		t.Fatalf("changed page must carry its body: %+v", same)
	}
}

func TestUnchangedWhenHashMatches(t *testing.T) {
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte("x")) }))
	defer origin.Close()
	plan := basePlan(origin.URL + "/a")
	// sha256("x")
	plan.Checkpoints[origin.URL+"/a"] = api.Checkpoint{ContentHash: "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"}
	cp := newFakeCP(t, plan)
	if _, err := Run(context.Background(), cp.opts()); err != nil {
		t.Fatal(err)
	}
	if p := cp.pages[0]; !p.Unchanged || len(p.Body) != 0 || p.ContentHash == "" {
		t.Fatalf("unchanged page must omit the body: %+v", p)
	}
	if got := cp.finished[0]["stats"].(map[string]any)["pages_unchanged"].(float64); got != 1 {
		t.Fatalf("pages_unchanged=%v", got)
	}
}

func TestHTTPFailuresAreReportedNotFatal(t *testing.T) {
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/gone" {
			http.NotFound(w, r)
			return
		}
		_, _ = w.Write([]byte("ok"))
	}))
	defer origin.Close()
	cp := newFakeCP(t, basePlan(origin.URL+"/gone", origin.URL+"/ok"))
	if _, err := Run(context.Background(), cp.opts()); err != nil {
		t.Fatal(err)
	}
	var failed api.Page
	for _, p := range cp.pages {
		if strings.HasSuffix(p.URL, "/gone") {
			failed = p
		}
	}
	if failed.Status != 404 || failed.Error != "HTTP 404" || len(failed.Body) != 0 {
		t.Fatalf("404 must be reported as an error page: %+v", failed)
	}
	st := cp.finished[0]["stats"].(map[string]any)
	if st["requests_failed"].(float64) != 1 || st["http_errors"].(float64) != 1 || st["pages_fetched"].(float64) != 2 {
		t.Fatalf("stats %v", st)
	}
	if cp.reason() != ReasonFinished {
		t.Fatal("per-URL failures do not change the close reason; the platform derives succeeded/partial/failed")
	}
}

func TestPageBudget(t *testing.T) {
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte("ok")) }))
	defer origin.Close()
	plan := basePlan(origin.URL + "/start")
	plan.Policy.MaxPages = 3
	cp := newFakeCP(t, plan)
	cp.pagesFn = func(pages []api.Page) (int, api.PagesResponse) {
		var f []api.Item
		for _, p := range pages {
			if strings.HasSuffix(p.URL, "/start") {
				for i := 0; i < 6; i++ {
					f = append(f, api.Item{URL: fmt.Sprintf("%s/n%d", origin.URL, i), Page: 1})
				}
			}
		}
		return 200, api.PagesResponse{Follow: f}
	}
	if _, err := Run(context.Background(), cp.opts()); err != nil {
		t.Fatal(err)
	}
	if len(cp.pages) != 3 || cp.reason() != ReasonPageBudget {
		t.Fatalf("pages=%d reason=%q", len(cp.pages), cp.finished[0]["reason"])
	}
	if cp.finished[0]["stats"].(map[string]any)["urls_over_budget"].(float64) != 4 {
		t.Fatalf("over-budget count: %v", cp.finished[0]["stats"])
	}
}

func TestPriorityOrdering(t *testing.T) {
	var order []string
	var mu sync.Mutex
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		order = append(order, r.URL.Path)
		mu.Unlock()
	}))
	defer origin.Close()
	plan := basePlan()
	plan.Policy.Concurrency = 1
	plan.StartURLs = []api.Item{{URL: origin.URL + "/low", Page: 1}, {URL: origin.URL + "/high", Page: 1, Priority: 5}, {URL: origin.URL + "/low2", Page: 1}}
	cp := newFakeCP(t, plan)
	if _, err := Run(context.Background(), cp.opts()); err != nil {
		t.Fatal(err)
	}
	if strings.Join(order, ",") != "/high,/low,/low2" {
		t.Fatalf("priority then FIFO expected, got %v", order)
	}
}

func TestOperatorCancel(t *testing.T) {
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-r.Context().Done():
		case <-time.After(10 * time.Second):
		}
	}))
	defer origin.Close()
	cp := newFakeCP(t, basePlan(origin.URL+"/slow"))
	cp.cancelAt.Store(true)
	start := time.Now()
	if _, err := Run(context.Background(), cp.opts()); err != nil {
		t.Fatal(err)
	}
	if cp.reason() != ReasonCancelled || time.Since(start) > 3*time.Second {
		t.Fatalf("reason=%v after %v", cp.finished[0]["reason"], time.Since(start))
	}
}

func TestJobTimeout(t *testing.T) {
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-r.Context().Done():
		case <-time.After(10 * time.Second):
		}
	}))
	defer origin.Close()
	plan := basePlan(origin.URL + "/slow")
	plan.Policy.TimeoutSeconds = 1
	cp := newFakeCP(t, plan)
	if _, err := Run(context.Background(), cp.opts()); err != nil {
		t.Fatal(err)
	}
	if cp.reason() != ReasonTimeout || cp.finished[0]["error"] == nil {
		t.Fatalf("timeout must be a failure with an error message: %v", cp.finished[0])
	}
}

func TestSignalTerminates(t *testing.T) {
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		<-r.Context().Done()
	}))
	defer origin.Close()
	cp := newFakeCP(t, basePlan(origin.URL+"/slow"))
	ctx, cancel := context.WithCancel(context.Background())
	time.AfterFunc(100*time.Millisecond, cancel)
	if _, err := Run(ctx, cp.opts()); err != nil {
		t.Fatal(err)
	}
	if cp.reason() != ReasonTerminated {
		t.Fatalf("reason %v", cp.finished[0]["reason"])
	}
}

func TestPlanRejectionReportsFailure(t *testing.T) {
	cp := newFakeCP(t, basePlan())
	cp.planCode = 422
	if _, err := Run(context.Background(), cp.opts()); err == nil {
		t.Fatal("expected an error")
	}
	if cp.reason() != ReasonException || !strings.Contains(cp.finished[0]["error"].(string), "could not start") {
		t.Fatalf("a claimed job must not be left running: %v", cp.finished)
	}
}

func TestInactiveJobIsNotFinished(t *testing.T) {
	cp := newFakeCP(t, basePlan())
	cp.planCode = 409 // already cancelled/finished elsewhere
	if _, err := Run(context.Background(), cp.opts()); err == nil {
		t.Fatal("expected an error")
	}
	if len(cp.finished) != 0 {
		t.Fatal("must not call finish for a job that is not ours any more")
	}
}

func TestIngestRejectionAbortsJob(t *testing.T) {
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte("ok")) }))
	defer origin.Close()
	cp := newFakeCP(t, basePlan(origin.URL+"/a"))
	cp.pagesFn = func([]api.Page) (int, api.PagesResponse) { return 409, api.PagesResponse{} }
	if _, err := Run(context.Background(), cp.opts()); err != nil {
		t.Fatal(err)
	}
	if cp.reason() != ReasonException || !strings.Contains(cp.finished[0]["error"].(string), "ingest failed") {
		t.Fatalf("%v", cp.finished[0])
	}
}

func TestTransientIngestErrorIsRetried(t *testing.T) {
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte("ok")) }))
	defer origin.Close()
	cp := newFakeCP(t, basePlan(origin.URL+"/a"))
	var calls atomic.Int32
	cp.pagesFn = func([]api.Page) (int, api.PagesResponse) {
		if calls.Add(1) == 1 {
			return 503, api.PagesResponse{}
		}
		return 200, api.PagesResponse{}
	}
	if _, err := Run(context.Background(), cp.opts()); err != nil {
		t.Fatal(err)
	}
	if cp.reason() != ReasonFinished || len(cp.pages) != 1 {
		t.Fatalf("503 from the control plane must be retried: pages=%d %v", len(cp.pages), cp.finished)
	}
}

func TestCancelStillFlushesAlreadyFetchedPages(t *testing.T) {
	var served atomic.Int32
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/fast" {
			served.Add(1)
			_, _ = w.Write([]byte("data"))
			return
		}
		<-r.Context().Done()
	}))
	defer origin.Close()
	plan := basePlan(origin.URL+"/fast", origin.URL+"/hang")
	cp := newFakeCP(t, plan)
	o := cp.opts()
	o.FlushEvery = time.Hour // nothing flushes before the cancel
	time.AfterFunc(300*time.Millisecond, func() { cp.cancelAt.Store(true) })
	if _, err := Run(context.Background(), o); err != nil {
		t.Fatal(err)
	}
	if cp.reason() != ReasonCancelled {
		t.Fatalf("reason %v", cp.finished[0]["reason"])
	}
	if len(cp.pages) != 1 || !strings.HasSuffix(cp.pages[0].URL, "/fast") {
		t.Fatalf("the page fetched before the cancel must still be delivered, got %d", len(cp.pages))
	}
}
