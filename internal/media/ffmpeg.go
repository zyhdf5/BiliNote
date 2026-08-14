package media

import (
	"context"
	"fmt"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

type Processor struct {
	Binary  string
	Timeout time.Duration
}

func (p *Processor) NormalizeAudio(parent context.Context, input, workDir string) (string, error) {
	binary := p.Binary
	if binary == "" {
		binary = "ffmpeg"
	}
	ctx := parent
	var cancel context.CancelFunc
	if p.Timeout > 0 {
		ctx, cancel = context.WithTimeout(parent, p.Timeout)
		defer cancel()
	}
	out := filepath.Join(workDir, "audio.wav")
	cmd := exec.CommandContext(ctx, binary, "-hide_banner", "-loglevel", "error", "-y", "-i", input, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", out)
	b, err := cmd.CombinedOutput()
	if err != nil {
		if ctx.Err() != nil {
			return "", ctx.Err()
		}
		return "", fmt.Errorf("ffmpeg failed: %w: %s", err, strings.TrimSpace(string(b)))
	}
	return out, nil
}
func (p *Processor) Check(ctx context.Context) error {
	binary := p.Binary
	if binary == "" {
		binary = "ffmpeg"
	}
	return exec.CommandContext(ctx, binary, "-version").Run()
}
