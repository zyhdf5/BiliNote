package video

import (
	"context"
	"errors"
	"net/url"
	"strings"

	"github.com/zyhdf5/bilinote-go/internal/transcript"
)

// BilibiliSource uses Bilibili's native view/player APIs first and falls back to
// yt-dlp when the native path is rate-limited/risk-controlled, unavailable, or
// has no usable subtitle track.
type BilibiliSource struct {
	Fallback Source
	Native   *BilibiliClient
	Guard    URLGuard
}

func NewBilibiliSource(fallback Source, guard URLGuard, opts BilibiliOptions) *BilibiliSource {
	return &BilibiliSource{
		Fallback: fallback,
		Native:   NewBilibiliClient(guard, opts),
		Guard:    guard,
	}
}

func (s *BilibiliSource) Name() string { return "bilibili" }

func (s *BilibiliSource) Supports(rawURL string) bool {
	u, err := url.Parse(rawURL)
	if err != nil {
		return false
	}
	return isBilibiliHost(u.Hostname())
}

func (s *BilibiliSource) Metadata(ctx context.Context, rawURL string) (*Meta, error) {
	if err := s.Guard.Validate(ctx, rawURL); err != nil {
		return nil, err
	}

	if s.Native != nil {
		m, err := s.Native.Metadata(ctx, rawURL)
		if err == nil {
			return m, nil
		}
		if !bilibiliFallbackAllowed(err) {
			return nil, err
		}
	}

	if s.Fallback == nil {
		return nil, errors.New("bilibili native metadata failed and no fallback is configured")
	}
	m, err := s.Fallback.Metadata(ctx, rawURL)
	if err != nil {
		return nil, err
	}
	m.Platform = "bilibili"
	return m, nil
}

func (s *BilibiliSource) Subtitle(ctx context.Context, meta *Meta, workDir string) (*transcript.Transcript, error) {
	if s.Native != nil && meta != nil && meta.BVID != "" && meta.CID > 0 {
		t, err := s.Native.Subtitle(ctx, meta)
		if err == nil && t != nil && len(t.Segments) > 0 {
			return t, nil
		}
		if err != nil && !bilibiliFallbackAllowed(err) {
			return nil, err
		}
	}

	if s.Fallback == nil {
		return nil, nil
	}
	return s.Fallback.Subtitle(ctx, meta, workDir)
}

func bilibiliFallbackAllowed(err error) bool {
	if err == nil {
		return true
	}
	return !errors.Is(err, context.Canceled) &&
		!errors.Is(err, context.DeadlineExceeded) &&
		!errors.Is(err, ErrBilibiliUnsafeRedirect)
}

func isBilibiliHost(host string) bool {
	host = strings.ToLower(strings.TrimSuffix(host, "."))
	return host == "bilibili.com" || strings.HasSuffix(host, ".bilibili.com") ||
		host == "b23.tv" || strings.HasSuffix(host, ".b23.tv")
}
