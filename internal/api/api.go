package api

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/zyhdf5/bilinote-go/internal/repository"
	"github.com/zyhdf5/bilinote-go/internal/task"
	"github.com/zyhdf5/bilinote-go/internal/video"
)

type API struct {
	Repo  *repository.Repository
	Guard video.URLGuard
}

func (a *API) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) { writeJSON(w, 200, map[string]any{"ok": true}) })
	mux.HandleFunc("GET /readyz", a.ready)
	mux.HandleFunc("POST /api/v1/extractions", a.createExtraction)
	// Compatibility route for existing summary consumers. New knowledge
	// ingestion should use /extractions so the pipeline skips the summary LLM.
	mux.HandleFunc("POST /api/v1/summaries", a.createSummary)
	mux.HandleFunc("GET /api/v1/tasks/{id}", a.get)
	mux.HandleFunc("POST /api/v1/tasks/{id}/cancel", a.cancel)
	return withRecover(withJSON(mux))
}

func (a *API) ready(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := contextWithTimeout(r, 3*time.Second)
	defer cancel()
	if err := a.Repo.Pool.Ping(ctx); err != nil {
		writeJSON(w, 503, map[string]any{"ok": false, "error": "postgres unavailable"})
		return
	}
	writeJSON(w, 200, map[string]any{"ok": true})
}

func (a *API) createExtraction(w http.ResponseWriter, r *http.Request) {
	a.create(w, r, task.KindExtraction)
}

func (a *API) createSummary(w http.ResponseWriter, r *http.Request) {
	a.create(w, r, task.KindSummary)
}

func (a *API) create(w http.ResponseWriter, r *http.Request, kind task.Kind) {
	var in struct {
		URL string `json:"url"`
	}
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&in); err != nil || strings.TrimSpace(in.URL) == "" {
		writeJSON(w, 400, map[string]any{"error": "invalid request"})
		return
	}
	if err := a.Guard.Validate(r.Context(), in.URL); err != nil {
		writeJSON(w, 400, map[string]any{"error": err.Error()})
		return
	}
	id := newID()
	t, err := a.Repo.Create(r.Context(), id, in.URL, kind)
	if err != nil {
		writeJSON(w, 500, map[string]any{"error": "create task failed"})
		return
	}
	writeJSON(w, 202, t)
}

func (a *API) get(w http.ResponseWriter, r *http.Request) {
	t, err := a.Repo.Get(r.Context(), r.PathValue("id"))
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			writeJSON(w, 404, map[string]any{"error": "not found"})
			return
		}
		writeJSON(w, 500, map[string]any{"error": "query failed"})
		return
	}
	out := map[string]any{"id": t.ID, "kind": t.Kind, "source_url": t.SourceURL, "platform": t.Platform, "source_id": t.SourceID, "title": t.Title, "status": t.Status, "stage": t.Stage, "progress": t.Progress, "transcript_source": t.TranscriptSource, "summary": t.Summary, "attempts": t.Attempts, "cancel_requested": t.CancelRequested, "error": t.Error, "created_at": t.CreatedAt, "started_at": t.StartedAt, "finished_at": t.FinishedAt}
	if string(t.Transcript) != "" && string(t.Transcript) != "null" {
		var v any
		if json.Unmarshal(t.Transcript, &v) == nil {
			out["transcript"] = v
		}
	}
	if string(t.Metadata) != "" && string(t.Metadata) != "null" {
		var v any
		if json.Unmarshal(t.Metadata, &v) == nil {
			out["video"] = v
		}
	}
	writeJSON(w, 200, out)
}

func (a *API) cancel(w http.ResponseWriter, r *http.Request) {
	if err := a.Repo.RequestCancel(r.Context(), r.PathValue("id")); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			writeJSON(w, 404, map[string]any{"error": "not found or not cancellable"})
			return
		}
		writeJSON(w, 500, map[string]any{"error": "cancel failed"})
		return
	}
	writeJSON(w, 202, map[string]any{"status": "cancellation_requested"})
}

func newID() string {
	b := make([]byte, 12)
	if _, err := rand.Read(b); err != nil {
		return hex.EncodeToString([]byte(time.Now().Format("150405.000000")))
	}
	return hex.EncodeToString(b)
}
func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
func withJSON(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		next.ServeHTTP(w, r)
	})
}
func withRecover(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if recover() != nil {
				writeJSON(w, 500, map[string]any{"error": "internal server error"})
			}
		}()
		next.ServeHTTP(w, r)
	})
}
func contextWithTimeout(r *http.Request, d time.Duration) (context.Context, context.CancelFunc) {
	return context.WithTimeout(r.Context(), d)
}
