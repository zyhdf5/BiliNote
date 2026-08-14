package llm

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

type Client struct {
	BaseURL, APIKey, Model string
	Temperature            float64
	MaxTokens, MaxRetries  int
	HTTP                   *http.Client
}
type message struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}
type request struct {
	Model       string    `json:"model"`
	Messages    []message `json:"messages"`
	Temperature float64   `json:"temperature"`
	MaxTokens   int       `json:"max_tokens,omitempty"`
}
type response struct {
	Choices []struct {
		Message message `json:"message"`
	} `json:"choices"`
}

func New(baseURL, apiKey, model string, temp float64, maxTokens int, timeout time.Duration, retries int) *Client {
	return &Client{BaseURL: strings.TrimRight(baseURL, "/"), APIKey: apiKey, Model: model, Temperature: temp, MaxTokens: maxTokens, MaxRetries: retries, HTTP: &http.Client{Timeout: timeout}}
}
func (c *Client) Generate(ctx context.Context, system, user string) (string, error) {
	var last error
	for i := 0; i <= c.MaxRetries; i++ {
		s, e := c.once(ctx, system, user)
		if e == nil {
			return s, nil
		}
		last = e
		if ctx.Err() != nil {
			return "", ctx.Err()
		}
		time.Sleep(time.Duration(i+1) * 500 * time.Millisecond)
	}
	return "", last
}
func (c *Client) once(ctx context.Context, system, user string) (string, error) {
	p := request{Model: c.Model, Temperature: c.Temperature, MaxTokens: c.MaxTokens, Messages: []message{{Role: "system", Content: system}, {Role: "user", Content: user}}}
	b, _ := json.Marshal(p)
	req, e := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+"/chat/completions", bytes.NewReader(b))
	if e != nil {
		return "", e
	}
	req.Header.Set("Content-Type", "application/json")
	if c.APIKey != "" {
		req.Header.Set("Authorization", "Bearer "+c.APIKey)
	}
	resp, e := c.HTTP.Do(req)
	if e != nil {
		return "", e
	}
	defer resp.Body.Close()
	rb, _ := io.ReadAll(io.LimitReader(resp.Body, 16<<20))
	if resp.StatusCode/100 != 2 {
		return "", fmt.Errorf("llm status %d: %s", resp.StatusCode, strings.TrimSpace(string(rb)))
	}
	var out response
	if e = json.Unmarshal(rb, &out); e != nil {
		return "", e
	}
	if len(out.Choices) == 0 {
		return "", fmt.Errorf("llm returned no choices")
	}
	return strings.TrimSpace(out.Choices[0].Message.Content), nil
}
