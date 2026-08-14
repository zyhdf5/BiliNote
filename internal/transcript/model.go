package transcript

type Source string

const (
	SourceSubtitle Source = "subtitle"
	SourceASR      Source = "asr"
)

type Segment struct {
	StartMS int64  `json:"start_ms"`
	EndMS   int64  `json:"end_ms"`
	Text    string `json:"text"`
}

type Transcript struct {
	Language string    `json:"language,omitempty"`
	Source   Source    `json:"source"`
	Provider string    `json:"provider,omitempty"`
	Segments []Segment `json:"segments"`
}

func (t *Transcript) PlainText() string {
	if t == nil {
		return ""
	}
	out := ""
	for _, s := range t.Segments {
		if s.Text == "" {
			continue
		}
		if out != "" {
			out += "\n"
		}
		out += s.Text
	}
	return out
}
