// Package job runs one crawl job: plan → concurrent fetch → batched ingest → finish.
package job

import (
	"container/heap"
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"net/url"
	"time"

	"emithran.com/mip/fetcher/internal/api"
	"emithran.com/mip/fetcher/internal/fetch"
)

// Close reasons understood by the platform's finalize() (same vocabulary as the Scrapy engine).
const (
	ReasonFinished   = "finished"
	ReasonPageBudget = "closespider_pagecount"
	ReasonTimeout    = "closespider_timeout"
	ReasonCancelled  = "cancelled"
	ReasonTerminated = "terminated"
	ReasonException  = "exception"
)

var (
	errCancelled = errors.New("cancelled by operator")
	errTimeout   = errors.New("job timeout")
)

const maxPool = 256

type Options struct {
	JobID          int
	API            *api.Client
	Transport      http.RoundTripper // nil → real network; fetch.Replay for fixtures
	Log            *slog.Logger
	HeartbeatEvery time.Duration // default 3s
	FlushEvery     time.Duration // default 250ms
	BatchPages     int           // default 25
	BatchBytes     int           // default 6 MiB of body data
	// Backoff overrides the plan's retry backoff (tests).
	BackoffBase, BackoffCap time.Duration
}

func (o *Options) defaults() {
	if o.HeartbeatEvery <= 0 {
		o.HeartbeatEvery = 3 * time.Second
	}
	if o.FlushEvery <= 0 {
		o.FlushEvery = 250 * time.Millisecond
	}
	if o.BatchPages <= 0 {
		o.BatchPages = 25
	}
	if o.BatchBytes <= 0 {
		o.BatchBytes = 6 << 20
	}
	if o.Log == nil {
		o.Log = slog.Default()
	}
}

type fetched struct {
	item api.Item
	res  fetch.Result
}

// Run executes the job and returns the final status the platform assigned.
func Run(parent context.Context, o Options) (string, error) {
	o.defaults()
	log := o.Log.With("job", o.JobID)

	plan, err := o.API.Plan(parent, o.JobID)
	if err != nil {
		var se *api.StatusError
		if !(errors.As(err, &se) && (se.Code == http.StatusNotFound || se.Code == http.StatusConflict)) {
			// we own a claimed job: never leave it "running" — report the failure
			_, _ = o.API.Finish(context.WithoutCancel(parent), o.JobID, ReasonException, "fetcher could not start: "+err.Error(), nil)
		}
		return "", fmt.Errorf("plan: %w", err)
	}
	pol := plan.Policy
	log.Info("plan received", "source", plan.Source, "start_urls", len(plan.StartURLs), "concurrency", pol.Concurrency,
		"max_pages", pol.MaxPages, "robots", pol.RespectRobots)

	run, cancel := context.WithCancelCause(parent)
	defer cancel(nil)
	if pol.TimeoutSeconds > 0 {
		t := time.AfterFunc(time.Duration(pol.TimeoutSeconds)*time.Second, func() { cancel(errTimeout) })
		defer t.Stop()
	}

	f := fetch.New(fetch.Policy{
		UserAgent: pol.UserAgent, ContactEmail: pol.ContactEmail, Accept: pol.Accept, Concurrency: pol.Concurrency,
		Delay: time.Duration(pol.DownloadDelay * float64(time.Second)), RespectRobots: pol.RespectRobots,
		MaxRetries: pol.MaxRetries, BackoffBase: o.BackoffBase, BackoffCap: o.BackoffCap,
	}, o.Transport)
	st := &stats{}
	logs := &logBuffer{}
	f.OnRetry = func(u string, attempt int, d time.Duration, reason string) {
		logs.add("info", fmt.Sprintf("retry %d in %s for %s (%s)", attempt, d.Round(time.Millisecond), u, reason))
	}
	logs.add("info", fmt.Sprintf("go fetcher started: %d start URL(s), %d worker(s)", len(plan.StartURLs), poolSize(plan)))

	rep := newReporter(run, cancel, &o, st, logs)
	repDone := make(chan struct{})
	go func() { defer close(repDone); rep.loop() }()

	fr := newFrontier(pol.MaxPages)
	for _, it := range plan.StartURLs {
		fr.add(it, st)
	}

	pool := poolSize(plan)
	work := make(chan api.Item, pool)
	results := make(chan fetched, pool)
	for i := 0; i < pool; i++ {
		go func() {
			for it := range work {
				cp := plan.Checkpoints[it.URL]
				r := f.Fetch(run, it.URL, fetch.Checkpoint{ETag: cp.ETag, LastModified: cp.LastModified, ContentHash: cp.ContentHash}, plan.Force)
				results <- fetched{it, r}
			}
		}()
	}

	inflight, pending := 0, 0
loop:
	for {
		for inflight < pool && fr.len() > 0 && run.Err() == nil {
			work <- fr.pop()
			inflight++
		}
		if inflight == 0 && fr.len() == 0 && pending == 0 {
			break
		}
		select {
		case r := <-results:
			inflight--
			if page, ok := toPage(r, st); ok {
				select {
				case rep.in <- page:
					pending++
				case <-run.Done():
				}
			}
		case ack := <-rep.acks:
			pending -= ack.pages
			for _, it := range ack.follow {
				fr.add(it, st)
			}
		case <-run.Done():
			break loop
		}
	}
	close(work)
	for ; inflight > 0; inflight-- { // workers observe the cancelled context and return promptly
		<-results
	}

	reason := ReasonFinished
	var failure string
	if cause := context.Cause(run); cause != nil {
		switch {
		case errors.Is(cause, errCancelled):
			reason = ReasonCancelled
		case errors.Is(cause, errTimeout):
			reason, failure = ReasonTimeout, fmt.Sprintf("job exceeded %ds timeout", pol.TimeoutSeconds)
		case errors.Is(cause, context.Canceled):
			reason, failure = ReasonTerminated, "fetcher terminated by signal"
		default:
			reason, failure = ReasonException, cause.Error()
		}
	} else if fr.overBudget {
		reason = ReasonPageBudget
	}

	// Flush what was already fetched, even after a cancel, using a context that outlives the run.
	flushCtx, stop := context.WithTimeout(context.WithoutCancel(parent), 30*time.Second)
	defer stop()
	close(rep.in)
	<-repDone
	rep.flushRemaining(flushCtx)

	logs.add("info", fmt.Sprintf("fetcher done (%s): %d fetched, %d unchanged, %d not modified, %d failed", reason,
		st.pagesFetched.Load(), st.pagesUnchanged.Load(), st.notModified.Load(), st.requestsFailed.Load()))
	_, _ = o.API.Heartbeat(flushCtx, o.JobID, st.snapshot(), logs.drain())
	status, err := o.API.Finish(flushCtx, o.JobID, reason, failure, st.snapshot())
	if err != nil {
		return "", fmt.Errorf("finish: %w", err)
	}
	log.Info("job finished", "status", status, "reason", reason)
	return status, nil
}

func poolSize(p *api.Plan) int {
	hosts := map[string]bool{}
	for _, d := range p.Policy.AllowedDomains {
		hosts[d] = true
	}
	for _, it := range p.StartURLs {
		if u, err := url.Parse(it.URL); err == nil {
			hosts[u.Host] = true
		}
	}
	n := max(1, p.Policy.Concurrency) * max(1, len(hosts))
	return min(n, maxPool)
}

// toPage converts a fetch result into the ingest payload and updates fetch-side stats.
// ok=false for results caused by our own cancellation (nothing meaningful to report).
func toPage(r fetched, st *stats) (api.Page, bool) {
	st.retries.Add(int64(r.res.Retries))
	p := api.Page{URL: r.item.URL, Depth: r.item.Depth, Page: max(1, r.item.Page), Parser: r.item.Parser, Meta: r.item.Meta}
	res := r.res
	if res.Err != nil {
		if errors.Is(res.Err, context.Canceled) || errors.Is(res.Err, context.DeadlineExceeded) {
			return p, false
		}
		st.requestsFailed.Add(1)
		p.Error = res.Err.Error()
		return p, true
	}
	p.Status = res.Status
	if res.Replay != nil {
		p.Replayed, p.Replay = true, res.Replay
	}
	if !res.FetchedAt.IsZero() {
		t := res.FetchedAt
		p.FetchedAt = &t
	}
	if res.NotModified {
		st.notModified.Add(1)
		p.NotModified = true
		return p, true
	}
	st.pagesFetched.Add(1)
	st.bytes.Add(int64(len(res.Body)))
	if res.Status >= 400 {
		st.httpErrors.Add(1)
		st.requestsFailed.Add(1)
		p.Error = fmt.Sprintf("HTTP %d", res.Status)
		return p, true
	}
	p.ContentHash, p.ETag, p.LastModified = res.Hash, res.Header.Get("ETag"), res.Header.Get("Last-Modified")
	p.ContentType = res.Header.Get("Content-Type")
	p.Headers = flatten(res.Header)
	if res.Unchanged {
		st.pagesUnchanged.Add(1)
		p.Unchanged = true
		return p, true
	}
	p.Body = res.Body
	return p, true
}

func flatten(h http.Header) map[string]string {
	out := make(map[string]string, len(h))
	for k, v := range h {
		if len(v) > 0 {
			out[k] = v[0]
		}
	}
	return out
}

// ---------------------------------------------------------------- frontier

type frontierItem struct {
	api.Item
	seq int
}

type pq []frontierItem

func (q pq) Len() int { return len(q) }
func (q pq) Less(i, j int) bool {
	if q[i].Priority != q[j].Priority {
		return q[i].Priority > q[j].Priority
	}
	return q[i].seq < q[j].seq
}
func (q pq) Swap(i, j int) { q[i], q[j] = q[j], q[i] }
func (q *pq) Push(x any)   { *q = append(*q, x.(frontierItem)) }
func (q *pq) Pop() any {
	old := *q
	it := old[len(old)-1]
	*q = old[:len(old)-1]
	return it
}

// frontier is the URL queue: dedupes, enforces the page budget, orders by priority then arrival.
// It is only touched by the coordinator goroutine.
type frontier struct {
	q          pq
	seen       map[string]bool
	budget     int
	scheduled  int
	seq        int
	overBudget bool
}

func newFrontier(budget int) *frontier {
	if budget <= 0 {
		budget = 50
	}
	return &frontier{seen: map[string]bool{}, budget: budget}
}

func (f *frontier) len() int { return len(f.q) }

func (f *frontier) add(it api.Item, st *stats) {
	if f.seen[it.URL] {
		st.urlsDuplicate.Add(1)
		return
	}
	if f.scheduled >= f.budget {
		st.urlsOverBudget.Add(1)
		f.overBudget = true
		return
	}
	f.seen[it.URL] = true
	f.scheduled++
	f.seq++
	st.urlsDiscovered.Add(1)
	st.urlsQueued.Add(1)
	heap.Push(&f.q, frontierItem{it, f.seq})
}

func (f *frontier) pop() api.Item { return heap.Pop(&f.q).(frontierItem).Item }
