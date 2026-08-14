package pipeline

import (
	"context"
	"fmt"
	"os"
	"time"

	"github.com/zyhdf5/bilinote-go/internal/asr"
	"github.com/zyhdf5/bilinote-go/internal/media"
	"github.com/zyhdf5/bilinote-go/internal/summary"
	"github.com/zyhdf5/bilinote-go/internal/task"
	"github.com/zyhdf5/bilinote-go/internal/transcript"
	"github.com/zyhdf5/bilinote-go/internal/video"
	"github.com/zyhdf5/bilinote-go/internal/workspace"
	"github.com/zyhdf5/bilinote-go/internal/ytdlp"
)

type StageUpdater func(stage string, progress int) error

type Result struct {
	Meta       *video.Meta
	Transcript *transcript.Transcript
	Summary    string
}
type Pipeline struct {
	Registry       *video.Registry
	YTDLP          *ytdlp.Runner
	Media          *media.Processor
	ASR            *asr.Client
	Summarizer     *summary.Summarizer
	WorkRoot       string
	PreferSubtitle bool
}

func (p *Pipeline) Run(ctx context.Context, t *task.Task, update StageUpdater) (*Result, error) {
	dir, err := workspace.New(p.WorkRoot, t.ID)
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(dir)
	step := func(s string, n int) error {
		if update == nil {
			return nil
		}
		return update(s, n)
	}
	if err = step("resolving", 5); err != nil {
		return nil, err
	}
	src, err := p.Registry.Resolve(t.SourceURL)
	if err != nil {
		return nil, err
	}
	if err = step("metadata", 10); err != nil {
		return nil, err
	}
	meta, err := src.Metadata(ctx, t.SourceURL)
	if err != nil {
		return nil, fmt.Errorf("metadata: %w", err)
	}
	if meta.URL == "" {
		meta.URL = t.SourceURL
	}
	var tr *transcript.Transcript
	if p.PreferSubtitle {
		_ = step("subtitle", 20)
		tr, err = src.Subtitle(ctx, meta, dir)
		if err != nil {
			tr = nil
		}
	}
	if tr == nil || len(tr.Segments) == 0 {
		_ = step("downloading", 30)
		audio, er := p.YTDLP.DownloadAudio(ctx, meta.URL, dir)
		if er != nil {
			return nil, fmt.Errorf("download audio: %w", er)
		}
		_ = step("audio_processing", 45)
		wav, er := p.Media.NormalizeAudio(ctx, audio, dir)
		if er != nil {
			return nil, fmt.Errorf("normalize audio: %w", er)
		}
		_ = os.Remove(audio)
		_ = step("transcribing", 60)
		tr, er = p.ASR.Transcribe(ctx, wav)
		_ = os.Remove(wav)
		if er != nil {
			return nil, fmt.Errorf("asr: %w", er)
		}
	}
	if tr == nil || len(tr.Segments) == 0 {
		return nil, fmt.Errorf("empty transcript")
	}
	_ = step("summarizing", 75)
	sum, err := p.Summarizer.Summarize(ctx, meta, tr)
	if err != nil {
		return nil, fmt.Errorf("summarize: %w", err)
	}
	_ = step("saving", 95)
	return &Result{Meta: meta, Transcript: tr, Summary: sum}, nil
}

var _ = time.Second
