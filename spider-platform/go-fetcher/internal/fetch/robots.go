package fetch

import (
	"context"
	"fmt"
	"net/url"
	"strings"
	"sync"

	"github.com/temoto/robotstxt"
)

type robotsEntry struct {
	mu     sync.Mutex
	loaded bool
	data   *robotstxt.RobotsData
}

// robotsAllowed applies Google's robots.txt semantics: 2xx → parse, 4xx → allow all,
// 5xx or network failure → refuse (never guess that crawling is permitted).
func (f *Fetcher) robotsAllowed(ctx context.Context, u *url.URL) (bool, error) {
	key := u.Scheme + "://" + u.Host
	f.mu.Lock()
	e := f.robots[key]
	if e == nil {
		e = &robotsEntry{}
		f.robots[key] = e
	}
	f.mu.Unlock()

	e.mu.Lock()
	defer e.mu.Unlock()
	if !e.loaded {
		robotsURL := key + "/robots.txt"
		h := f.host(u.Host)
		if err := h.acquire(ctx, f.policy.Delay); err != nil {
			return false, err
		}
		status, _, body, err := f.do(ctx, robotsURL, Checkpoint{}, true)
		h.release()
		if err != nil {
			if ctx.Err() != nil {
				return false, ctx.Err() // not cached: a cancelled load must not poison the entry
			}
			return false, fmt.Errorf("robots.txt unavailable for %s: %w", u.Host, err)
		}
		data, err := robotstxt.FromStatusAndBytes(status, body)
		if err != nil {
			return false, fmt.Errorf("robots.txt unparsable for %s: %w", u.Host, err)
		}
		e.data, e.loaded = data, true
	}
	// TestAgent (not FindGroup().Test) is what applies the whole-file allow/disallow-all rules for 4xx/5xx
	return e.data.TestAgent(u.RequestURI(), agentToken(f.policy.UserAgent)), nil
}

// agentToken is the product token robots.txt groups are matched against ("Name/1.0 (+url)" → "Name").
func agentToken(ua string) string {
	if i := strings.IndexAny(ua, "/ "); i > 0 {
		return ua[:i]
	}
	return ua
}
