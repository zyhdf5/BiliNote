package task

import "time"

type Kind string

const (
	KindExtraction Kind = "extraction"
	KindSummary    Kind = "summary"
)

type Status string

const (
	StatusQueued    Status = "queued"
	StatusRunning   Status = "running"
	StatusSucceeded Status = "succeeded"
	StatusFailed    Status = "failed"
	StatusCancelled Status = "cancelled"
)

type Task struct {
	ID               string     `json:"id"`
	Kind             Kind       `json:"kind"`
	SourceURL        string     `json:"source_url"`
	Platform         string     `json:"platform,omitempty"`
	SourceID         string     `json:"source_id,omitempty"`
	Title            string     `json:"title,omitempty"`
	Status           Status     `json:"status"`
	Stage            string     `json:"stage"`
	Progress         int        `json:"progress"`
	TranscriptSource string     `json:"transcript_source,omitempty"`
	Summary          string     `json:"summary,omitempty"`
	Transcript       []byte     `json:"-"`
	Metadata         []byte     `json:"-"`
	Attempts         int        `json:"attempts"`
	CancelRequested  bool       `json:"cancel_requested"`
	Error            string     `json:"error,omitempty"`
	CreatedAt        time.Time  `json:"created_at"`
	StartedAt        *time.Time `json:"started_at,omitempty"`
	FinishedAt       *time.Time `json:"finished_at,omitempty"`
}

func (t *Task) IsExtraction() bool {
	return t != nil && t.Kind == KindExtraction
}
