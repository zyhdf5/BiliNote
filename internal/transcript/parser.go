package transcript

import (
	"bufio"
	"os"
	"regexp"
	"strconv"
	"strings"
)

var timingRE = regexp.MustCompile(`(?:(\d+):)?(\d{2}):(\d{2})[\.,](\d{3})\s+-->\s+(?:(\d+):)?(\d{2}):(\d{2})[\.,](\d{3})`)
var tagRE = regexp.MustCompile(`<[^>]+>`)

func ParseSubtitleFile(path string) (*Transcript, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var segs []Segment
	s := bufio.NewScanner(f)
	s.Buffer(make([]byte, 64*1024), 2*1024*1024)
	var start, end int64
	var text []string
	inCue := false
	flush := func() {
		if !inCue {
			return
		}
		t := strings.TrimSpace(tagRE.ReplaceAllString(strings.Join(text, " "), ""))
		if t != "" {
			segs = append(segs, Segment{StartMS: start, EndMS: end, Text: t})
		}
		text = nil
		inCue = false
	}
	for s.Scan() {
		line := strings.TrimSpace(s.Text())
		if m := timingRE.FindStringSubmatch(line); m != nil {
			flush()
			start = parseTime(m[1:5])
			end = parseTime(m[5:9])
			inCue = true
			continue
		}
		if line == "" {
			flush()
			continue
		}
		if inCue {
			text = append(text, line)
		}
	}
	flush()
	if err := s.Err(); err != nil {
		return nil, err
	}
	return &Transcript{Source: SourceSubtitle, Segments: segs}, nil
}

func parseTime(p []string) int64 {
	n := func(v string) int64 { x, _ := strconv.ParseInt(v, 10, 64); return x }
	return ((n(p[0])*60+n(p[1]))*60+n(p[2]))*1000 + n(p[3])
}
