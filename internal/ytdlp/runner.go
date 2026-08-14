package ytdlp

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

type Runner struct {
	Binary          string
	CookiesFile     string
	Proxy           string
	Retries         int
	FragmentRetries int
	Timeout         time.Duration
	sem             chan struct{}
}

type Metadata struct {
	ID          string  `json:"id"`
	Title       string  `json:"title"`
	Description string  `json:"description"`
	Uploader    string  `json:"uploader"`
	Duration    float64 `json:"duration"`
	WebpageURL  string  `json:"webpage_url"`
	Extractor   string  `json:"extractor"`
	Language    string  `json:"language"`
}

func New(binary, cookiesFile, proxy string, retries, fragmentRetries int, timeout time.Duration, concurrency int) *Runner {
	if binary == "" {
		binary = "yt-dlp"
	}
	if concurrency <= 0 {
		concurrency = 1
	}
	return &Runner{Binary: binary, CookiesFile: cookiesFile, Proxy: proxy, Retries: retries, FragmentRetries: fragmentRetries, Timeout: timeout, sem: make(chan struct{}, concurrency)}
}

func (r *Runner) Probe(ctx context.Context, rawURL string) (*Metadata, error) {
	out, err := r.run(ctx, "--dump-single-json", "--skip-download", "--no-warnings", rawURL)
	if err != nil {
		return nil, err
	}
	var m Metadata
	if err := json.Unmarshal(out, &m); err != nil {
		return nil, fmt.Errorf("decode yt-dlp metadata: %w", err)
	}
	return &m, nil
}

func (r *Runner) DownloadSubtitle(ctx context.Context, rawURL, workDir string, languages []string) (string, error) {
	if err := os.MkdirAll(workDir, 0o755); err != nil {
		return "", err
	}
	langs := "all"
	if len(languages) > 0 {
		langs = strings.Join(languages, ",")
	}
	_, err := r.run(ctx,
		"--skip-download", "--write-subs", "--write-auto-subs",
		"--sub-langs", langs, "--sub-format", "vtt/srt/best",
		"-o", filepath.Join(workDir, "subtitle.%(ext)s"), rawURL,
	)
	if err != nil {
		return "", err
	}
	matches, _ := filepath.Glob(filepath.Join(workDir, "subtitle.*"))
	for _, p := range matches {
		if strings.HasSuffix(p, ".vtt") || strings.HasSuffix(p, ".srt") {
			return p, nil
		}
	}
	return "", os.ErrNotExist
}

func (r *Runner) DownloadAudio(ctx context.Context, rawURL, workDir string) (string, error) {
	if err := os.MkdirAll(workDir, 0o755); err != nil {
		return "", err
	}
	_, err := r.run(ctx, "-f", "bestaudio/best", "--no-playlist", "-o", filepath.Join(workDir, "source.%(ext)s"), rawURL)
	if err != nil {
		return "", err
	}
	matches, _ := filepath.Glob(filepath.Join(workDir, "source.*"))
	if len(matches) == 0 {
		return "", fmt.Errorf("yt-dlp completed but no audio file found")
	}
	return matches[0], nil
}

func (r *Runner) Check(ctx context.Context) error {
	_, err := r.run(ctx, "--version")
	return err
}

func (r *Runner) run(parent context.Context, args ...string) ([]byte, error) {
	select {
	case r.sem <- struct{}{}:
		defer func() { <-r.sem }()
	case <-parent.Done():
		return nil, parent.Err()
	}
	ctx := parent
	var cancel context.CancelFunc
	if r.Timeout > 0 {
		ctx, cancel = context.WithTimeout(parent, r.Timeout)
		defer cancel()
	}
	base := []string{"--no-progress", "--newline", "--retries", fmt.Sprint(r.Retries), "--fragment-retries", fmt.Sprint(r.FragmentRetries)}
	if r.CookiesFile != "" {
		base = append(base, "--cookies", r.CookiesFile)
	}
	if r.Proxy != "" {
		base = append(base, "--proxy", r.Proxy)
	}
	base = append(base, args...)
	cmd := exec.CommandContext(ctx, r.Binary, base...)
	out, err := cmd.CombinedOutput()
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		msg := strings.TrimSpace(string(out))
		if len(msg) > 4000 {
			msg = msg[len(msg)-4000:]
		}
		return nil, fmt.Errorf("yt-dlp failed: %w: %s", err, msg)
	}
	return out, nil
}
