// Package api is the client for the platform's /api/fetcher control plane.
package api

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

type Item struct {
	URL      string         `json:"url"`
	Depth    int            `json:"depth"`
	Page     int            `json:"page"`
	Parser   *string        `json:"parser,omitempty"`
	Meta     map[string]any `json:"meta,omitempty"`
	Priority int            `json:"priority,omitempty"`
}

type Checkpoint struct {
	ETag         string `json:"etag"`
	LastModified string `json:"last_modified"`
	ContentHash  string `json:"content_hash"`
}

type Policy struct {
	UserAgent      string   `json:"user_agent"`
	ContactEmail   string   `json:"contact_email"`
	Concurrency    int      `json:"concurrency"`
	DownloadDelay  float64  `json:"download_delay"`
	RespectRobots  bool     `json:"respect_robots"`
	AllowedDomains []string `json:"allowed_domains"`
	MaxPages       int      `json:"max_pages"`
	TimeoutSeconds int      `json:"timeout_seconds"`
	Accept         string   `json:"accept"`
	MaxRetries     int      `json:"max_retries"`
}

type Plan struct {
	JobID       int                   `json:"job_id"`
	Source      string                `json:"source"`
	Force       bool                  `json:"force"`
	StartURLs   []Item                `json:"start_urls"`
	Checkpoints map[string]Checkpoint `json:"checkpoints"`
	Policy      Policy                `json:"policy"`
}

// Page is one fetched URL as the platform expects it (see mip/fetchapi/ingest.py PageIn).
type Page struct {
	URL          string            `json:"url"`
	Depth        int               `json:"depth"`
	Page         int               `json:"page"`
	Parser       *string           `json:"parser,omitempty"`
	Meta         map[string]any    `json:"meta,omitempty"`
	Status       int               `json:"status"`
	ContentType  string            `json:"content_type,omitempty"`
	Headers      map[string]string `json:"headers,omitempty"`
	Body         []byte            `json:"body_b64,omitempty"` // encoding/json emits standard base64
	ContentHash  string            `json:"content_hash,omitempty"`
	ETag         string            `json:"etag,omitempty"`
	LastModified string            `json:"last_modified,omitempty"`
	FetchedAt    *time.Time        `json:"fetched_at,omitempty"`
	NotModified  bool              `json:"not_modified,omitempty"`
	Unchanged    bool              `json:"unchanged,omitempty"`
	Error        string            `json:"error,omitempty"`
	Replayed     bool              `json:"replayed,omitempty"`
	Replay       map[string]any    `json:"replay,omitempty"`
}

type PagesResponse struct {
	Follow []Item         `json:"follow"`
	Stats  map[string]int `json:"stats"`
}

type LogLine struct {
	Level   string    `json:"level"`
	Message string    `json:"message"`
	TS      time.Time `json:"ts"`
}

// StatusError is a non-2xx answer from the control plane.
type StatusError struct {
	Code int
	Body string
}

func (e *StatusError) Error() string {
	return fmt.Sprintf("control plane returned %d: %s", e.Code, e.Body)
}

// Fatal reports whether retrying cannot help (job gone / not active / rejected).
func (e *StatusError) Fatal() bool { return e.Code >= 400 && e.Code < 500 && e.Code != 429 }

type Client struct {
	Base  string
	Token string
	HTTP  *http.Client
	// Backoff is the first retry delay for transient failures (doubles each attempt).
	Backoff time.Duration
	Retries int
}

func New(base, token string) *Client {
	return &Client{Base: strings.TrimRight(base, "/"), Token: token, HTTP: &http.Client{Timeout: 5 * time.Minute},
		Backoff: 500 * time.Millisecond, Retries: 4}
}

func (c *Client) call(ctx context.Context, method, path string, in, out any) error {
	var payload []byte
	if in != nil {
		var err error
		if payload, err = json.Marshal(in); err != nil {
			return err
		}
	}
	delay := c.Backoff
	for attempt := 0; ; attempt++ {
		err := c.once(ctx, method, path, payload, out)
		if err == nil {
			return nil
		}
		var se *StatusError
		fatal := ctx.Err() != nil || (errors.As(err, &se) && se.Fatal())
		if fatal || attempt >= c.Retries {
			return err
		}
		select {
		case <-time.After(delay):
			delay *= 2
		case <-ctx.Done():
			return ctx.Err()
		}
	}
}

func (c *Client) once(ctx context.Context, method, path string, payload []byte, out any) error {
	req, err := http.NewRequestWithContext(ctx, method, c.Base+path, bytes.NewReader(payload))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	if c.Token != "" {
		req.Header.Set("X-Admin-Token", c.Token)
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}
	if resp.StatusCode/100 != 2 {
		msg := string(body)
		if len(msg) > 500 {
			msg = msg[:500]
		}
		return &StatusError{Code: resp.StatusCode, Body: msg}
	}
	if out != nil {
		return json.Unmarshal(body, out)
	}
	return nil
}

func (c *Client) Plan(ctx context.Context, job int) (*Plan, error) {
	var p Plan
	if err := c.call(ctx, http.MethodGet, fmt.Sprintf("/api/fetcher/jobs/%d/plan", job), nil, &p); err != nil {
		return nil, err
	}
	return &p, nil
}

func (c *Client) Pages(ctx context.Context, job int, pages []Page) (*PagesResponse, error) {
	var r PagesResponse
	err := c.call(ctx, http.MethodPost, fmt.Sprintf("/api/fetcher/jobs/%d/pages", job), map[string]any{"pages": pages}, &r)
	return &r, err
}

// Heartbeat reports fetch-side stats and log lines; cancel is true when the job should stop.
func (c *Client) Heartbeat(ctx context.Context, job int, stats map[string]int64, logs []LogLine) (cancel bool, err error) {
	var r struct {
		Cancel bool `json:"cancel"`
	}
	err = c.call(ctx, http.MethodPost, fmt.Sprintf("/api/fetcher/jobs/%d/heartbeat", job),
		map[string]any{"stats": stats, "logs": logs}, &r)
	return r.Cancel, err
}

// Finish closes the job; the platform decides the final status (succeeded/partial/failed/cancelled).
func (c *Client) Finish(ctx context.Context, job int, reason, errMsg string, stats map[string]int64) (string, error) {
	var r struct {
		Status string `json:"status"`
	}
	body := map[string]any{"reason": reason, "stats": stats}
	if errMsg != "" {
		body["error"] = errMsg
	}
	err := c.call(ctx, http.MethodPost, fmt.Sprintf("/api/fetcher/jobs/%d/finish", job), body, &r)
	return r.Status, err
}
