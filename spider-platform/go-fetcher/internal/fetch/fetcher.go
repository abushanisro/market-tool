// Package fetch is the HTTP engine: per-host politeness, conditional GET, bounded retries with
// exponential backoff + jitter (honouring Retry-After), robots.txt and content hashing.
package fetch

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/rand/v2"
	"net/http"
	"net/url"
	"strconv"
	"sync"
	"time"
)

// Policy mirrors the "policy" block of the plan returned by the control plane.
type Policy struct {
	UserAgent     string
	ContactEmail  string
	Accept        string
	Concurrency   int           // max in-flight requests per host
	Delay         time.Duration // mean spacing between requests to one host (randomised 0.5x–1.5x)
	RespectRobots bool
	MaxRetries    int
	BackoffBase   time.Duration
	BackoffCap    time.Duration
	// RateLimitFloor is the minimum wait after HTTP 429 when the server gave no Retry-After (grows with each attempt, capped by BackoffCap).
	RateLimitFloor time.Duration
	RequestTimeout time.Duration
	MaxBody        int64
}

func (p *Policy) defaults() {
	if p.Concurrency < 1 {
		p.Concurrency = 1
	}
	if p.BackoffBase <= 0 {
		p.BackoffBase = 2 * time.Second
	}
	if p.BackoffCap <= 0 {
		p.BackoffCap = 120 * time.Second
	}
	if p.RateLimitFloor <= 0 {
		p.RateLimitFloor = 30 * time.Second
	}
	if p.RequestTimeout <= 0 {
		p.RequestTimeout = 60 * time.Second
	}
	if p.MaxBody <= 0 {
		p.MaxBody = 200 << 20
	}
}

// Checkpoint is what the platform remembers about a URL from the previous run.
type Checkpoint struct {
	ETag         string
	LastModified string
	ContentHash  string
}

// Result is the outcome of fetching one URL (after retries).
type Result struct {
	URL         string
	Status      int // 0 → no response (see Err)
	Header      http.Header
	Body        []byte
	Hash        string // sha256 of the decoded body
	NotModified bool   // 304 for a conditional request
	Unchanged   bool   // 200 whose body hash equals the checkpoint
	Replay      map[string]any
	Retries     int
	Err         error
	FetchedAt   time.Time
}

var (
	// ErrRobots marks a URL refused by robots.txt.
	ErrRobots = errors.New("blocked by robots.txt")
	// ErrBodyTooLarge marks a response above Policy.MaxBody.
	ErrBodyTooLarge = errors.New("response body exceeds size limit")
)

var retryStatus = map[int]bool{408: true, 425: true, 429: true, 500: true, 502: true, 503: true, 504: true, 522: true, 524: true}

// Fetcher is safe for concurrent use.
type Fetcher struct {
	policy Policy
	client *http.Client
	// OnRetry, if set, is called before sleeping for a retry.
	OnRetry func(rawURL string, attempt int, delay time.Duration, reason string)

	mu     sync.Mutex
	hosts  map[string]*host
	robots map[string]*robotsEntry
}

// New builds a Fetcher. rt may be nil (default transport) or a replay transport.
func New(p Policy, rt http.RoundTripper) *Fetcher {
	p.defaults()
	if rt == nil {
		t := http.DefaultTransport.(*http.Transport).Clone()
		t.MaxIdleConnsPerHost = p.Concurrency * 2
		rt = t
	}
	return &Fetcher{
		policy: p,
		client: &http.Client{Transport: rt, Timeout: p.RequestTimeout},
		hosts:  map[string]*host{},
		robots: map[string]*robotsEntry{},
	}
}

// host paces and bounds requests to one origin.
type host struct {
	sem  chan struct{}
	mu   sync.Mutex
	next time.Time
}

func (f *Fetcher) host(h string) *host {
	f.mu.Lock()
	defer f.mu.Unlock()
	st := f.hosts[h]
	if st == nil {
		st = &host{sem: make(chan struct{}, f.policy.Concurrency)}
		f.hosts[h] = st
	}
	return st
}

// acquire blocks until a slot is free and the politeness delay has elapsed.
func (h *host) acquire(ctx context.Context, delay time.Duration) error {
	select {
	case h.sem <- struct{}{}:
	case <-ctx.Done():
		return ctx.Err()
	}
	h.mu.Lock()
	at := h.next
	if now := time.Now(); at.Before(now) {
		at = now
	}
	h.next = at.Add(time.Duration(float64(delay) * (0.5 + rand.Float64())))
	h.mu.Unlock()
	if d := time.Until(at); d > 0 {
		t := time.NewTimer(d)
		defer t.Stop()
		select {
		case <-t.C:
		case <-ctx.Done():
			h.release()
			return ctx.Err()
		}
	}
	return nil
}

func (h *host) release() { <-h.sem }

// penalize pushes back every later request to the host (429/503 with Retry-After).
func (h *host) penalize(d time.Duration) {
	h.mu.Lock()
	if at := time.Now().Add(d); at.After(h.next) {
		h.next = at
	}
	h.mu.Unlock()
}

// Fetch retrieves rawURL. force skips conditional headers and the unchanged check.
func (f *Fetcher) Fetch(ctx context.Context, rawURL string, cp Checkpoint, force bool) Result {
	res := Result{URL: rawURL}
	u, err := url.Parse(rawURL)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		res.Err = fmt.Errorf("invalid URL %q", rawURL)
		return res
	}
	if f.policy.RespectRobots {
		ok, err := f.robotsAllowed(ctx, u)
		if err != nil {
			res.Err = err
			return res
		}
		if !ok {
			res.Err = ErrRobots
			return res
		}
	}
	h := f.host(u.Host)
	for attempt := 0; ; attempt++ {
		status, hdr, body, err := f.once(ctx, h, rawURL, cp, force)
		res.Retries = attempt
		retry, reason := false, ""
		switch {
		case ctx.Err() != nil:
			res.Err = ctx.Err()
			return res
		case err != nil:
			retry, reason = !errors.Is(err, ErrBodyTooLarge), err.Error()
			res.Err = err
		case retryStatus[status]:
			retry, reason = true, fmt.Sprintf("HTTP %d", status)
			res.Err = nil
			res.Status, res.Header, res.Body = status, hdr, body
		default:
			res.Err = nil
			f.fill(&res, status, hdr, body, cp, force)
			return res
		}
		if !retry || attempt >= f.policy.MaxRetries {
			return res
		}
		delay := f.backoff(attempt, hdr)
		if status == http.StatusTooManyRequests && hdr.Get("Retry-After") == "" {
			// "slow down" with no hint: retrying in seconds only deepens the throttle. Back off hard, and make every worker wait.
			delay = max(delay, min(f.policy.RateLimitFloor*time.Duration(attempt+1), f.policy.BackoffCap))
		}
		h.penalize(delay)
		if f.OnRetry != nil {
			f.OnRetry(rawURL, attempt+1, delay, reason)
		}
		if !sleep(ctx, delay) {
			res.Err = ctx.Err()
			return res
		}
	}
}

func (f *Fetcher) fill(res *Result, status int, hdr http.Header, body []byte, cp Checkpoint, force bool) {
	res.Status, res.Header, res.Body, res.FetchedAt = status, hdr, body, time.Now().UTC()
	if status == http.StatusNotModified {
		res.NotModified, res.Body = true, nil
		return
	}
	sum := sha256.Sum256(body)
	res.Hash = hex.EncodeToString(sum[:])
	if status == http.StatusOK && !force && cp.ContentHash != "" && cp.ContentHash == res.Hash {
		res.Unchanged = true
	}
	if raw := hdr.Get("X-Emithran-Replay"); raw != "" {
		res.Replay = map[string]any{}
		_ = json.Unmarshal([]byte(raw), &res.Replay)
		hdr.Del("X-Emithran-Replay")
	}
}

func (f *Fetcher) once(ctx context.Context, h *host, rawURL string, cp Checkpoint, force bool) (int, http.Header, []byte, error) {
	if err := h.acquire(ctx, f.policy.Delay); err != nil {
		return 0, nil, nil, err
	}
	defer h.release()
	return f.do(ctx, rawURL, cp, force)
}

func (f *Fetcher) do(ctx context.Context, rawURL string, cp Checkpoint, force bool) (int, http.Header, []byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return 0, nil, nil, err
	}
	req.Header.Set("User-Agent", f.policy.UserAgent)
	req.Header.Set("Accept-Language", "en")
	if f.policy.Accept != "" {
		req.Header.Set("Accept", f.policy.Accept)
	}
	if f.policy.ContactEmail != "" {
		req.Header.Set("From", f.policy.ContactEmail)
	}
	if !force {
		if cp.ETag != "" {
			req.Header.Set("If-None-Match", cp.ETag)
		}
		if cp.LastModified != "" {
			req.Header.Set("If-Modified-Since", cp.LastModified)
		}
	}
	resp, err := f.client.Do(req)
	if err != nil {
		return 0, nil, nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, f.policy.MaxBody+1))
	if err != nil {
		return 0, nil, nil, err
	}
	if int64(len(body)) > f.policy.MaxBody {
		return 0, nil, nil, ErrBodyTooLarge
	}
	return resp.StatusCode, resp.Header, body, nil
}

// backoff = min(cap, base·2^attempt) · U(0.5, 1.0), or Retry-After when the server sent one.
func (f *Fetcher) backoff(attempt int, hdr http.Header) time.Duration {
	if ra := hdr.Get("Retry-After"); ra != "" {
		if secs, err := strconv.ParseFloat(ra, 64); err == nil {
			return min(f.policy.BackoffCap, time.Duration(secs*float64(time.Second)))
		}
		if t, err := http.ParseTime(ra); err == nil {
			return max(0, min(f.policy.BackoffCap, time.Until(t)))
		}
	}
	d := f.policy.BackoffBase << min(attempt, 20)
	d = min(d, f.policy.BackoffCap)
	return time.Duration(float64(d) * (0.5 + rand.Float64()/2))
}

func sleep(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-t.C:
		return true
	case <-ctx.Done():
		return false
	}
}
