package transcript

import (
	"os"
	"path/filepath"
	"testing"
)

func TestParseVTT(t *testing.T) {
	d := t.TempDir()
	p := filepath.Join(d, "x.vtt")
	data := "WEBVTT\n\n00:00:01.000 --> 00:00:03.500\nhello <b>world</b>\n\n00:00:04.000 --> 00:00:05.000\nnext\n"
	if err := os.WriteFile(p, []byte(data), 0o600); err != nil {
		t.Fatal(err)
	}
	tr, err := ParseSubtitleFile(p)
	if err != nil {
		t.Fatal(err)
	}
	if len(tr.Segments) != 2 {
		t.Fatalf("segments=%d", len(tr.Segments))
	}
	if tr.Segments[0].StartMS != 1000 || tr.Segments[0].EndMS != 3500 {
		t.Fatalf("bad timing: %+v", tr.Segments[0])
	}
	if tr.Segments[0].Text != "hello world" {
		t.Fatalf("text=%q", tr.Segments[0].Text)
	}
}
