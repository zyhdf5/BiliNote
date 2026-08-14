package video

import (
	"context"
	"errors"

	"github.com/zyhdf5/bilinote-go/internal/transcript"
)

var ErrUnsupportedSource = errors.New("unsupported video source")

type Source interface {
	Name() string
	Supports(rawURL string) bool
	Metadata(ctx context.Context, rawURL string) (*Meta, error)
	Subtitle(ctx context.Context, meta *Meta, workDir string) (*transcript.Transcript, error)
}

type Registry struct{ sources []Source }

func NewRegistry(sources ...Source) *Registry { return &Registry{sources: sources} }

func (r *Registry) Resolve(rawURL string) (Source, error) {
	for _, s := range r.sources {
		if s.Supports(rawURL) {
			return s, nil
		}
	}
	return nil, ErrUnsupportedSource
}
