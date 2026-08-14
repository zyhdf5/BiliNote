package video

type Meta struct {
	Platform    string `json:"platform"`
	VideoID     string `json:"video_id"`
	URL         string `json:"url"`
	Title       string `json:"title"`
	Description string `json:"description,omitempty"`
	Author      string `json:"author,omitempty"`
	DurationMS  int64  `json:"duration_ms,omitempty"`
	Language    string `json:"language,omitempty"`

	// Bilibili-specific fields. They stay empty for other sources.
	BVID      string `json:"bvid,omitempty"`
	CID       int64  `json:"cid,omitempty"`
	Part      int    `json:"part,omitempty"`
	CoverURL  string `json:"cover_url,omitempty"`
	Extractor string `json:"extractor,omitempty"`
}
