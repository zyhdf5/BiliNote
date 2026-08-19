package worker

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"sync"
	"time"

	"github.com/zyhdf5/bilinote-go/internal/pipeline"
	"github.com/zyhdf5/bilinote-go/internal/repository"
	"github.com/zyhdf5/bilinote-go/internal/task"
)

type WorkerPool struct {
	Repo           *repository.Repository
	Pipeline       *pipeline.Pipeline
	Owner          string
	Concurrency    int
	PollInterval   time.Duration
	LeaseDuration  time.Duration
	MaxAttempts    int
	KeepTranscript bool
	Log            *slog.Logger
}

func (w *WorkerPool) Run(ctx context.Context) {
	if w.Concurrency <= 0 {
		w.Concurrency = 1
	}
	var wg sync.WaitGroup
	for i := 0; i < w.Concurrency; i++ {
		wg.Add(1)
		go func(n int) { defer wg.Done(); w.loop(ctx, fmt.Sprintf("%s-%d", w.Owner, n)) }(i + 1)
	}
	<-ctx.Done()
	wg.Wait()
}

func (w *WorkerPool) loop(ctx context.Context, owner string) {
	for {
		if ctx.Err() != nil {
			return
		}
		t, err := w.Repo.Claim(ctx, owner, w.LeaseDuration)
		if err != nil {
			if repository.IsNoRows(err) {
				select {
				case <-ctx.Done():
					return
				case <-time.After(w.PollInterval):
					continue
				}
			}
			w.Log.Error("claim task failed", "worker", owner, "error", err)
			select {
			case <-ctx.Done():
				return
			case <-time.After(w.PollInterval):
				continue
			}
		}
		w.process(ctx, owner, t)
	}
}

func (w *WorkerPool) process(parent context.Context, owner string, t *task.Task) {
	ctx, cancel := context.WithCancel(parent)
	defer cancel()

	leaseDone := make(chan struct{})
	go func() {
		ticker := time.NewTicker(max(10*time.Second, w.LeaseDuration/3))
		defer ticker.Stop()
		for {
			select {
			case <-leaseDone:
				return
			case <-ctx.Done():
				return
			case <-ticker.C:
				_ = w.Repo.Lease(context.Background(), t.ID, owner, w.LeaseDuration)
				cancelled, err := w.Repo.CancelRequested(context.Background(), t.ID)
				if err == nil && cancelled {
					cancel()
				}
			}
		}
	}()

	result, err := w.Pipeline.Run(ctx, t, func(stage string, progress int) error {
		cancelled, er := w.Repo.CancelRequested(ctx, t.ID)
		if er != nil && !errors.Is(er, context.Canceled) {
			return er
		}
		if cancelled {
			cancel()
			return context.Canceled
		}
		return w.Repo.UpdateStage(ctx, t.ID, stage, progress)
	})
	close(leaseDone)

	cancelled, _ := w.Repo.CancelRequested(context.Background(), t.ID)
	if cancelled || errors.Is(err, context.Canceled) {
		_ = w.Repo.MarkCancelled(context.Background(), t.ID)
		w.Log.Info("task cancelled", "task_id", t.ID)
		return
	}
	if err != nil {
		retry := t.Attempts < w.MaxAttempts
		_ = w.Repo.Fail(context.Background(), t.ID, err.Error(), retry, time.Duration(t.Attempts)*5*time.Second, w.MaxAttempts)
		w.Log.Error("task failed", "task_id", t.ID, "attempt", t.Attempts, "retry", retry, "error", err)
		return
	}
	keepTranscript := w.KeepTranscript || t.IsExtraction()
	if err := w.Repo.Succeed(context.Background(), t.ID, result.Meta, result.Transcript, result.Summary, keepTranscript); err != nil {
		_ = w.Repo.Fail(context.Background(), t.ID, "save result: "+err.Error(), true, 5*time.Second, w.MaxAttempts)
		return
	}
	w.Log.Info("task succeeded", "task_id", t.ID, "kind", t.Kind, "platform", result.Meta.Platform, "video_id", result.Meta.VideoID)
}
