package job

import (
	"context"
	"fmt"
	"sync"
	"sync/atomic"
	"time"

	"emithran.com/fetcher/internal/api"
)

// stats are the counters the fetcher owns (the platform counts items itself).
type stats struct {
	urlsDiscovered, urlsQueued, urlsDuplicate, urlsOverBudget atomic.Int64
	pagesFetched, pagesUnchanged, notModified                 atomic.Int64
	requestsFailed, httpErrors, retries, bytes                atomic.Int64
}

func (s *stats) snapshot() map[string]int64 {
	return map[string]int64{
		"urls_discovered": s.urlsDiscovered.Load(), "urls_queued": s.urlsQueued.Load(),
		"urls_duplicate": s.urlsDuplicate.Load(), "urls_over_budget": s.urlsOverBudget.Load(),
		"pages_fetched": s.pagesFetched.Load(), "pages_unchanged": s.pagesUnchanged.Load(),
		"not_modified": s.notModified.Load(), "requests_failed": s.requestsFailed.Load(),
		"http_errors": s.httpErrors.Load(), "retries": s.retries.Load(), "bytes": s.bytes.Load(),
	}
}

type logBuffer struct {
	mu    sync.Mutex
	lines []api.LogLine
}

func (b *logBuffer) add(level, msg string) {
	b.mu.Lock()
	b.lines = append(b.lines, api.LogLine{Level: level, Message: msg, TS: time.Now().UTC()})
	b.mu.Unlock()
}

func (b *logBuffer) drain() []api.LogLine {
	b.mu.Lock()
	defer b.mu.Unlock()
	out := b.lines
	b.lines = nil
	if out == nil {
		out = []api.LogLine{} // JSON must carry [] not null: the control plane rejects a null list
	}
	return out
}

type ack struct {
	pages  int
	follow []api.Item
}

// reporter is the only goroutine that talks to the control plane for a running job, so calls for one
// job are strictly sequential (stats written by heartbeat and ingest can never interleave).
type reporter struct {
	ctx    context.Context
	cancel context.CancelCauseFunc
	o      *Options
	st     *stats
	logs   *logBuffer

	in   chan api.Page
	acks chan ack

	buf      []api.Page
	bufBytes int
}

func newReporter(ctx context.Context, cancel context.CancelCauseFunc, o *Options, st *stats, logs *logBuffer) *reporter {
	return &reporter{ctx: ctx, cancel: cancel, o: o, st: st, logs: logs,
		in: make(chan api.Page, 256), acks: make(chan ack, 4096)}
}

func (r *reporter) loop() {
	flush := time.NewTicker(r.o.FlushEvery)
	beat := time.NewTicker(r.o.HeartbeatEvery)
	defer flush.Stop()
	defer beat.Stop()
	for {
		select {
		case p, ok := <-r.in:
			if !ok {
				return
			}
			r.buf = append(r.buf, p)
			r.bufBytes += len(p.Body)
			if len(r.buf) >= r.o.BatchPages || r.bufBytes >= r.o.BatchBytes {
				r.send(r.ctx)
			}
		case <-flush.C:
			r.send(r.ctx)
		case <-beat.C:
			cancel, err := r.o.API.Heartbeat(r.ctx, r.o.JobID, r.st.snapshot(), r.logs.drain())
			switch {
			case err != nil && r.ctx.Err() == nil:
				r.o.Log.Warn("heartbeat failed", "err", err) // transient; the platform's stale-job recovery is the backstop
			case cancel:
				r.cancel(errCancelled)
			}
		case <-r.ctx.Done():
			// run is over (cancel/timeout): keep what is already fetched, post it via flushRemaining
			for p := range r.in {
				r.buf = append(r.buf, p)
			}
			return
		}
	}
}

// send posts the buffered pages. Anything the platform rejects permanently aborts the job.
func (r *reporter) send(ctx context.Context) {
	if len(r.buf) == 0 {
		return
	}
	batch := r.buf
	r.buf, r.bufBytes = nil, 0
	resp, err := r.o.API.Pages(ctx, r.o.JobID, batch)
	if err != nil {
		if ctx.Err() != nil {
			r.buf = append(batch, r.buf...) // interrupted, not rejected: keep for the final flush
			return
		}
		r.cancel(fmt.Errorf("ingest failed: %w", err))
		return
	}
	r.acks <- ack{pages: len(batch), follow: resp.Follow}
}

// flushRemaining posts pages still buffered after the run ended (cancel/timeout/finish).
func (r *reporter) flushRemaining(ctx context.Context) { r.send(ctx) }
