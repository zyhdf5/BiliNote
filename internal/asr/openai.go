package asr

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/zyhdf5/bilinote-go/internal/transcript"
)

type Client struct {
	BaseURL    string
	APIKey     string
	Model      string
	Language   string
	HTTP       *http.Client
	MaxRetries int
	sem        chan struct{}
}

type verboseResponse struct {
	Language string `json:"language"`
	Text     string `json:"text"`
	Segments []struct {
		Start float64 `json:"start"`
		End   float64 `json:"end"`
		Text  string  `json:"text"`
	} `json:"segments"`
}

func New(baseURL, apiKey, model, language string, timeout time.Duration, retries, concurrency int) *Client {
	if concurrency <= 0 {
		concurrency = 1
	}
	return &Client{BaseURL: strings.TrimRight(baseURL, "/"), APIKey: apiKey, Model: model, Language: language, HTTP: &http.Client{Timeout: timeout}, MaxRetries: retries, sem: make(chan struct{}, concurrency)}
}

func (c *Client) Transcribe(ctx context.Context, audioPath string) (*transcript.Transcript, error) {
	select {
	case c.sem <- struct{}{}:
		defer func() { <-c.sem }()
	case <-ctx.Done():
		return nil, ctx.Err()
	}
	var last error
	for i := 0; i <= c.MaxRetries; i++ {
		t, err := c.once(ctx, audioPath)
		if err == nil {
			return t, nil
		}
		last = err
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		time.Sleep(time.Duration(i+1) * 500 * time.Millisecond)
	}
	return nil, last
}

func (c *Client) once(ctx context.Context, audioPath string) (*transcript.Transcript, error) {
	f, err := os.Open(audioPath)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var body bytes.Buffer
	w := multipart.NewWriter(&body)
	part, err := w.CreateFormFile("file", filepath.Base(audioPath))
	if err != nil {
		return nil, err
	}
	if _, err = io.Copy(part, f); err != nil {
		return nil, err
	}
	_ = w.WriteField("model", c.Model)
	_ = w.WriteField("response_format", "verbose_json")
	if c.Language != "" {
		_ = w.WriteField("language", c.Language)
	}
	if err = w.Close(); err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+"/audio/transcriptions", &body)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", w.FormDataContentType())
	if c.APIKey != "" {
		req.Header.Set("Authorization", "Bearer "+c.APIKey)
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if resp.StatusCode/100 != 2 {
		return nil, fmt.Errorf("asr status %d: %s", resp.StatusCode, strings.TrimSpace(string(b)))
	}
	var vr verboseResponse
	if err := json.Unmarshal(b, &vr); err != nil {
		return nil, fmt.Errorf("decode asr response: %w", err)
	}
	t := &transcript.Transcript{Language: vr.Language, Source: transcript.SourceASR, Provider: "openai-compatible"}
	for _, s := range vr.Segments {
		t.Segments = append(t.Segments, transcript.Segment{StartMS: int64(s.Start * 1000), EndMS: int64(s.End * 1000), Text: strings.TrimSpace(s.Text)})
	}
	if len(t.Segments) == 0 && strings.TrimSpace(vr.Text) != "" {
		t.Segments = []transcript.Segment{{Text: strings.TrimSpace(vr.Text)}}
	}
	return t, nil
}
