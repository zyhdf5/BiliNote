package repository

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/zyhdf5/bilinote-go/internal/task"
	"github.com/zyhdf5/bilinote-go/internal/transcript"
	"github.com/zyhdf5/bilinote-go/internal/video"
)

type Repository struct{ Pool *pgxpool.Pool }

func Open(ctx context.Context, dsn string) (*Repository, error) {
	if dsn == "" {
		return nil, fmt.Errorf("postgres dsn is empty")
	}
	p, e := pgxpool.New(ctx, dsn)
	if e != nil {
		return nil, e
	}
	if e = p.Ping(ctx); e != nil {
		p.Close()
		return nil, e
	}
	return &Repository{Pool: p}, nil
}
func (r *Repository) Close() { r.Pool.Close() }
func (r *Repository) Migrate(ctx context.Context, path string) error {
	b, e := os.ReadFile(path)
	if e != nil {
		return e
	}
	_, e = r.Pool.Exec(ctx, string(b))
	return e
}
func (r *Repository) Create(ctx context.Context, id, url string) (*task.Task, error) {
	_, e := r.Pool.Exec(ctx, `INSERT INTO video_tasks(id,source_url,status,stage,progress) VALUES($1,$2,'queued','queued',0)`, id, url)
	if e != nil {
		return nil, e
	}
	return r.Get(ctx, id)
}
func (r *Repository) Get(ctx context.Context, id string) (*task.Task, error) {
	row := r.Pool.QueryRow(ctx, `SELECT id,source_url,COALESCE(platform,''),COALESCE(source_id,''),COALESCE(title,''),status,stage,progress,COALESCE(transcript_source,''),COALESCE(summary,''),COALESCE(transcript,'null'::jsonb),COALESCE(metadata,'null'::jsonb),attempts,cancel_requested,COALESCE(error,''),created_at,started_at,finished_at FROM video_tasks WHERE id=$1`, id)
	var t task.Task
	var status string
	if e := row.Scan(&t.ID, &t.SourceURL, &t.Platform, &t.SourceID, &t.Title, &status, &t.Stage, &t.Progress, &t.TranscriptSource, &t.Summary, &t.Transcript, &t.Metadata, &t.Attempts, &t.CancelRequested, &t.Error, &t.CreatedAt, &t.StartedAt, &t.FinishedAt); e != nil {
		return nil, e
	}
	t.Status = task.Status(status)
	return &t, nil
}
func (r *Repository) RequestCancel(ctx context.Context, id string) error {
	tag, e := r.Pool.Exec(ctx, `UPDATE video_tasks SET cancel_requested=true,updated_at=now() WHERE id=$1 AND status IN ('queued','running')`, id)
	if e != nil {
		return e
	}
	if tag.RowsAffected() == 0 {
		return pgx.ErrNoRows
	}
	return nil
}
func (r *Repository) CancelRequested(ctx context.Context, id string) (bool, error) {
	var v bool
	e := r.Pool.QueryRow(ctx, `SELECT cancel_requested FROM video_tasks WHERE id=$1`, id).Scan(&v)
	return v, e
}
func (r *Repository) Claim(ctx context.Context, owner string, lease time.Duration) (*task.Task, error) {
	tx, e := r.Pool.BeginTx(ctx, pgx.TxOptions{})
	if e != nil {
		return nil, e
	}
	defer tx.Rollback(ctx)
	row := tx.QueryRow(ctx, `SELECT id FROM video_tasks WHERE cancel_requested=false AND ((status='queued' AND (next_retry_at IS NULL OR next_retry_at<=now())) OR (status='running' AND lease_until<now())) ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1`)
	var id string
	if e = row.Scan(&id); e != nil {
		return nil, e
	}
	_, e = tx.Exec(ctx, `UPDATE video_tasks SET status='running',stage='starting',progress=1,attempts=attempts+1,lease_owner=$2,lease_until=now()+($3::bigint * interval '1 millisecond'),started_at=COALESCE(started_at,now()),updated_at=now() WHERE id=$1`, id, owner, lease.Milliseconds())
	if e != nil {
		return nil, e
	}
	if e = tx.Commit(ctx); e != nil {
		return nil, e
	}
	return r.Get(ctx, id)
}
func (r *Repository) Lease(ctx context.Context, id, owner string, lease time.Duration) error {
	_, e := r.Pool.Exec(ctx, `UPDATE video_tasks SET lease_until=now()+($3::bigint * interval '1 millisecond'),updated_at=now() WHERE id=$1 AND lease_owner=$2 AND status='running'`, id, owner, lease.Milliseconds())
	return e
}
func (r *Repository) UpdateStage(ctx context.Context, id, stage string, progress int) error {
	_, e := r.Pool.Exec(ctx, `UPDATE video_tasks SET stage=$2,progress=$3,updated_at=now() WHERE id=$1`, id, stage, progress)
	return e
}
func (r *Repository) Succeed(ctx context.Context, id string, m *video.Meta, t *transcript.Transcript, summary string, keepTranscript bool) error {
	mb, _ := json.Marshal(m)
	var tb any = nil
	if keepTranscript {
		b, _ := json.Marshal(t)
		tb = string(b)
	}
	_, e := r.Pool.Exec(ctx, `UPDATE video_tasks SET platform=$2,source_id=$3,title=$4,status='succeeded',stage='done',progress=100,transcript_source=$5,summary=$6,metadata=$7::jsonb,transcript=$8::jsonb,finished_at=now(),lease_owner=NULL,lease_until=NULL,error=NULL,updated_at=now() WHERE id=$1`, id, m.Platform, m.VideoID, m.Title, string(t.Source), summary, string(mb), tb)
	return e
}
func (r *Repository) Fail(ctx context.Context, id, msg string, retry bool, delay time.Duration, maxAttempts int) error {
	if retry {
		_, e := r.Pool.Exec(ctx, `UPDATE video_tasks SET status=CASE WHEN attempts<$3 THEN 'queued' ELSE 'failed' END,stage=CASE WHEN attempts<$3 THEN 'retrying' ELSE 'failed' END,progress=CASE WHEN attempts<$3 THEN progress ELSE 100 END,error=$2,next_retry_at=CASE WHEN attempts<$3 THEN now()+($4::bigint*interval '1 millisecond') ELSE NULL END,lease_owner=NULL,lease_until=NULL,finished_at=CASE WHEN attempts<$3 THEN NULL ELSE now() END,updated_at=now() WHERE id=$1`, id, msg, maxAttempts, delay.Milliseconds())
		return e
	}
	_, e := r.Pool.Exec(ctx, `UPDATE video_tasks SET status='failed',stage='failed',progress=100,error=$2,lease_owner=NULL,lease_until=NULL,finished_at=now(),updated_at=now() WHERE id=$1`, id, msg)
	return e
}
func (r *Repository) MarkCancelled(ctx context.Context, id string) error {
	_, e := r.Pool.Exec(ctx, `UPDATE video_tasks SET status='cancelled',stage='cancelled',progress=100,lease_owner=NULL,lease_until=NULL,finished_at=now(),updated_at=now() WHERE id=$1`, id)
	return e
}
func IsNoRows(err error) bool { return errors.Is(err, pgx.ErrNoRows) }
