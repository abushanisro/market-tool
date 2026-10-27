package fetch

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
)

type replayRecord struct {
	File        string            `json:"file"`
	Status      int               `json:"status"`
	ContentType string            `json:"content_type"`
	Headers     map[string]string `json:"headers"`
	CapturedAt  string            `json:"captured_at"`
	RecordedURL string            `json:"recorded_url"`
	Note        string            `json:"note"`
}

// Replay serves recorded responses instead of touching the network — same index.json format as the
// Scrapy ReplayMiddleware (MIP_REPLAY_DIR), so both engines can run against one set of fixtures.
// Unknown URLs get a synthetic 404; robots.txt is 404 unless recorded.
type Replay struct {
	root  string
	index map[string]replayRecord
}

func NewReplay(dir string) (*Replay, error) {
	raw, err := os.ReadFile(filepath.Join(dir, "index.json"))
	if err != nil {
		return nil, fmt.Errorf("replay index: %w", err)
	}
	r := &Replay{root: dir, index: map[string]replayRecord{}}
	if err := json.Unmarshal(raw, &r.index); err != nil {
		return nil, fmt.Errorf("replay index: %w", err)
	}
	return r, nil
}

func (r *Replay) RoundTrip(req *http.Request) (*http.Response, error) {
	resp := func(status int, ct string, body []byte) *http.Response {
		return &http.Response{StatusCode: status, Status: http.StatusText(status), Header: http.Header{"Content-Type": {ct}},
			Body: io.NopCloser(bytes.NewReader(body)), ContentLength: int64(len(body)), Request: req}
	}
	rec, ok := r.index[req.URL.String()]
	if !ok {
		if req.URL.Path == "/robots.txt" {
			return resp(404, "text/plain", nil), nil
		}
		return resp(404, "text/plain", []byte("not recorded")), nil
	}
	body, err := os.ReadFile(filepath.Join(r.root, rec.File))
	if err != nil {
		return nil, err
	}
	status := rec.Status
	if status == 0 {
		status = 200
	}
	ct := rec.ContentType
	if ct == "" {
		ct = "application/octet-stream"
	}
	out := resp(status, ct, body)
	for k, v := range rec.Headers {
		out.Header.Set(k, v)
	}
	meta := map[string]string{}
	for k, v := range map[string]string{"captured_at": rec.CapturedAt, "recorded_url": rec.RecordedURL, "note": rec.Note} {
		if v != "" {
			meta[k] = v
		}
	}
	enc, _ := json.Marshal(meta)
	out.Header.Set("X-Mip-Replay", string(enc))
	return out, nil
}
