package video

import (
	"context"
	"net/url"
	"strings"

	"github.com/zyhdf5/bilinote-go/internal/transcript"
	"github.com/zyhdf5/bilinote-go/internal/ytdlp"
)

type GenericYTDLPSource struct {
	Runner    *ytdlp.Runner
	Guard     URLGuard
	Languages []string
}

func (s *GenericYTDLPSource) Name() string { return "yt-dlp" }
func (s *GenericYTDLPSource) Supports(rawURL string) bool {
	u, err := url.Parse(rawURL)
	return err == nil && (u.Scheme == "http" || u.Scheme == "https") && u.Host != ""
}
func (s *GenericYTDLPSource) Metadata(ctx context.Context, rawURL string) (*Meta, error) {
	if err := s.Guard.Validate(ctx, rawURL); err != nil {
		return nil, err
	}
	m, err := s.Runner.Probe(ctx, rawURL)
	if err != nil {
		return nil, err
	}
	platform := strings.ToLower(m.Extractor)
	if platform == "" {
		platform = "generic"
	}
	return &Meta{Platform: platform, VideoID: m.ID, URL: m.WebpageURL, Title: m.Title, Description: m.Description, Author: m.Uploader, DurationMS: int64(m.Duration * 1000), Language: m.Language}, nil
}
func (s *GenericYTDLPSource) Subtitle(ctx context.Context, meta *Meta, workDir string) (*transcript.Transcript, error) {
	p, err := s.Runner.DownloadSubtitle(ctx, meta.URL, workDir, s.Languages)
	if err != nil {
		return nil, err
	}
	t, err := transcript.ParseSubtitleFile(p)
	if err == nil {
		t.Language = meta.Language
		t.Provider = "yt-dlp"
	}
	return t, err
}
