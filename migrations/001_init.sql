CREATE TABLE IF NOT EXISTS video_tasks (
    id                  VARCHAR(32) PRIMARY KEY,
    source_url          TEXT NOT NULL,
    platform            VARCHAR(32),
    source_id           VARCHAR(255),
    title               TEXT,
    status              VARCHAR(32) NOT NULL,
    stage               VARCHAR(32) NOT NULL,
    progress            INTEGER NOT NULL DEFAULT 0,
    transcript_source   VARCHAR(32),
    summary             TEXT,
    transcript          JSONB,
    metadata            JSONB,
    attempts            INTEGER NOT NULL DEFAULT 0,
    next_retry_at       TIMESTAMPTZ,
    lease_owner         VARCHAR(128),
    lease_until         TIMESTAMPTZ,
    cancel_requested    BOOLEAN NOT NULL DEFAULT FALSE,
    error               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_video_tasks_claim
ON video_tasks (status, next_retry_at, lease_until, created_at);
