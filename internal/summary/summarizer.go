package summary

import (
	"context"
	"fmt"
	"strings"
	"sync"

	"github.com/zyhdf5/bilinote-go/internal/llm"
	"github.com/zyhdf5/bilinote-go/internal/transcript"
	"github.com/zyhdf5/bilinote-go/internal/video"
)

type Summarizer struct {
	LLM                        *llm.Client
	ChunkChars, MapConcurrency int
	MapPrompt, ReducePrompt    string
}

func (s *Summarizer) Summarize(ctx context.Context, meta *video.Meta, t *transcript.Transcript) (string, error) {
	text := t.PlainText()
	if strings.TrimSpace(text) == "" {
		return "", fmt.Errorf("empty transcript")
	}
	chunks := chunk(text, s.ChunkChars)
	if len(chunks) == 1 {
		return s.LLM.Generate(ctx, s.ReducePrompt, "视频标题："+meta.Title+"\n\n转录内容：\n"+chunks[0])
	}
	summaries := make([]string, len(chunks))
	sem := make(chan struct{}, max(1, s.MapConcurrency))
	var wg sync.WaitGroup
	var mu sync.Mutex
	var first error
	for i, c := range chunks {
		i, c := i, c
		wg.Add(1)
		go func() {
			defer wg.Done()
			select {
			case sem <- struct{}{}:
				defer func() { <-sem }()
			case <-ctx.Done():
				mu.Lock()
				if first == nil {
					first = ctx.Err()
				}
				mu.Unlock()
				return
			}
			out, e := s.LLM.Generate(ctx, s.MapPrompt, fmt.Sprintf("视频标题：%s\n片段 %d/%d：\n%s", meta.Title, i+1, len(chunks), c))
			mu.Lock()
			defer mu.Unlock()
			if e != nil && first == nil {
				first = e
			}
			summaries[i] = out
		}()
	}
	wg.Wait()
	if first != nil {
		return "", first
	}
	return s.LLM.Generate(ctx, s.ReducePrompt, "视频标题："+meta.Title+"\n\n阶段性总结：\n\n"+strings.Join(summaries, "\n\n---\n\n"))
}
func chunk(s string, n int) []string {
	if n <= 0 || len([]rune(s)) <= n {
		return []string{s}
	}
	r := []rune(s)
	out := make([]string, 0, (len(r)+n-1)/n)
	for len(r) > 0 {
		take := n
		if len(r) < take {
			take = len(r)
		}
		end := take
		if take < len(r) {
			for i := take; i > take*3/4; i-- {
				if r[i-1] == '\n' || r[i-1] == '。' {
					end = i
					break
				}
			}
		}
		out = append(out, string(r[:end]))
		r = r[end:]
	}
	return out
}
