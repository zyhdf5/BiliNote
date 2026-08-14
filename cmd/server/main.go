package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/zyhdf5/bilinote-go/internal/api"
	"github.com/zyhdf5/bilinote-go/internal/asr"
	"github.com/zyhdf5/bilinote-go/internal/config"
	"github.com/zyhdf5/bilinote-go/internal/llm"
	"github.com/zyhdf5/bilinote-go/internal/media"
	"github.com/zyhdf5/bilinote-go/internal/pipeline"
	"github.com/zyhdf5/bilinote-go/internal/repository"
	"github.com/zyhdf5/bilinote-go/internal/summary"
	"github.com/zyhdf5/bilinote-go/internal/video"
	"github.com/zyhdf5/bilinote-go/internal/worker"
	"github.com/zyhdf5/bilinote-go/internal/workspace"
	"github.com/zyhdf5/bilinote-go/internal/ytdlp"
)

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	cfgPath := os.Getenv("CONFIG_FILE")
	if cfgPath == "" {
		cfgPath = "config.yaml"
	}
	cfg, err := config.Load(cfgPath)
	if err != nil {
		log.Error("load config failed", "error", err)
		os.Exit(1)
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	if err := os.MkdirAll(cfg.Workspace.Root, 0o755); err != nil {
		fatal(log, "create workspace", err)
	}
	_ = workspace.CleanupStale(cfg.Workspace.Root, cfg.Workspace.StaleAfter)

	repo, err := repository.Open(ctx, cfg.Postgres.DSN)
	if err != nil {
		fatal(log, "open postgres", err)
	}
	defer repo.Close()
	migration := os.Getenv("MIGRATION_FILE")
	if migration == "" {
		migration = filepath.Join("migrations", "001_init.sql")
	}
	if err := repo.Migrate(ctx, migration); err != nil {
		fatal(log, "migrate", err)
	}

	ytdlpRunner := ytdlp.New(cfg.YTDLP.Binary, cfg.Video.CookiesFile, cfg.Video.Proxy, cfg.YTDLP.Retries, cfg.YTDLP.FragmentRetries, cfg.YTDLP.Timeout, cfg.YTDLP.Concurrency)
	guard := video.URLGuard{AllowedDomains: cfg.Video.AllowedDomains, AllowPrivateURLs: cfg.Video.AllowPrivateURLs, AllowUnlistedDomain: cfg.Video.AllowUnlistedDomain}
	generic := &video.GenericYTDLPSource{Runner: ytdlpRunner, Guard: guard, Languages: cfg.Video.SubtitleLanguages}
	bilibili := video.NewBilibiliSource(generic, guard, video.BilibiliOptions{Cookie: cfg.Bilibili.Cookie, Timeout: cfg.Bilibili.RequestTimeout, Retries: cfg.Bilibili.Retries, RetryBackoff: cfg.Bilibili.RetryBackoff})
	registry := video.NewRegistry(bilibili, generic)
	asrClient := asr.New(cfg.ASR.BaseURL, cfg.ASR.APIKey, cfg.ASR.Model, cfg.ASR.Language, cfg.ASR.Timeout, cfg.ASR.MaxRetries, cfg.ASR.Concurrency)
	llmClient := llm.New(cfg.LLM.BaseURL, cfg.LLM.APIKey, cfg.LLM.Model, cfg.LLM.Temperature, cfg.LLM.MaxTokens, cfg.LLM.Timeout, cfg.LLM.MaxRetries)
	mapPrompt, err := os.ReadFile(filepath.Join("prompts", "map.md"))
	if err != nil {
		fatal(log, "read map prompt", err)
	}
	reducePrompt, err := os.ReadFile(filepath.Join("prompts", "reduce.md"))
	if err != nil {
		fatal(log, "read reduce prompt", err)
	}
	summarizer := &summary.Summarizer{LLM: llmClient, ChunkChars: cfg.Summary.ChunkChars, MapConcurrency: cfg.Summary.MapConcurrency, MapPrompt: string(mapPrompt), ReducePrompt: string(reducePrompt)}
	pipe := &pipeline.Pipeline{Registry: registry, YTDLP: ytdlpRunner, Media: &media.Processor{Binary: cfg.FFmpeg.Binary, Timeout: cfg.FFmpeg.Timeout}, ASR: asrClient, Summarizer: summarizer, WorkRoot: cfg.Workspace.Root, PreferSubtitle: cfg.Video.PreferSubtitle}

	host, _ := os.Hostname()
	if host == "" {
		host = "bilinote"
	}
	workers := &worker.WorkerPool{Repo: repo, Pipeline: pipe, Owner: host, Concurrency: cfg.Worker.Concurrency, PollInterval: cfg.Worker.PollInterval, LeaseDuration: cfg.Worker.LeaseDuration, MaxAttempts: cfg.Worker.MaxAttempts, KeepTranscript: cfg.Summary.KeepTranscript, Log: log}
	go workers.Run(ctx)
	go cleanupLoop(ctx, cfg.Workspace.Root, cfg.Workspace.StaleAfter, log)

	a := &api.API{Repo: repo, Guard: guard}
	srv := &http.Server{Addr: cfg.Server.Addr, Handler: a.Handler(), ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 60 * time.Second}
	go func() {
		log.Info("server started", "addr", cfg.Server.Addr)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			fatal(log, "http server", err)
		}
	}()

	<-ctx.Done()
	shutdownCtx, cancel := context.WithTimeout(context.Background(), cfg.Server.ShutdownTimeout)
	defer cancel()
	_ = srv.Shutdown(shutdownCtx)
	log.Info("server stopped")
}

func cleanupLoop(ctx context.Context, root string, age time.Duration, log *slog.Logger) {
	ticker := time.NewTicker(time.Hour)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if err := workspace.CleanupStale(root, age); err != nil {
				log.Warn("workspace cleanup failed", "error", err)
			}
		}
	}
}
func fatal(log *slog.Logger, msg string, err error) {
	log.Error(msg, "error", err)
	panic(fmt.Sprintf("%s: %v", msg, err))
}
