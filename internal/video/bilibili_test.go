package video

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/zyhdf5/bilinote-go/internal/transcript"
)

type fakeSource struct {
	meta       *Meta
	transcript *transcript.Transcript
	metaCalls  int
	subCalls   int
}

func (f *fakeSource) Name() string         { return "fake" }
func (f *fakeSource) Supports(string) bool { return true }
func (f *fakeSource) Metadata(context.Context, string) (*Meta, error) {
	f.metaCalls++
	if f.meta == nil {
		return &Meta{Platform: "yt-dlp", URL: "https://www.bilibili.com/video/BV1abcdefghi"}, nil
	}
	return f.meta, nil
}
func (f *fakeSource) Subtitle(context.Context, *Meta, string) (*transcript.Transcript, error) {
	f.subCalls++
	return f.transcript, nil
}

func testBilibiliClient() *BilibiliClient {
	guard := URLGuard{AllowPrivateURLs: true, AllowUnlistedDomain: true}
	return NewBilibiliClient(guard, BilibiliOptions{Timeout: time.Second, Retries: 0, RetryBackoff: time.Millisecond})
}

func TestPickBilibiliTrackPrefersManualChinese(t *testing.T) {
	tracks := []bilibiliSubtitleTrack{
		{Language: "en", SubtitleURL: "en"},
		{Language: "ai-zh", SubtitleURL: "ai", AIType: 1},
		{Language: "zh-Hans", SubtitleURL: "manual", AIType: 0},
	}
	got := pickBilibiliTrack(tracks)
	if got == nil || got.SubtitleURL != "manual" {
		t.Fatalf("expected manual Chinese subtitle, got %#v", got)
	}
}

func TestBilibiliClientRecognizesHTTP412(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "risk", http.StatusPreconditionFailed)
	}))
	defer srv.Close()

	c := testBilibiliClient()
	c.PlayerEndpoint = srv.URL
	_, err := c.Subtitle(context.Background(), &Meta{BVID: "BV1abcdefghi", CID: 123})
	if !errors.Is(err, ErrBilibiliRiskControl) {
		t.Fatalf("expected risk-control error, got %v", err)
	}
}

func TestBilibiliSourceFallsBackOnHTTP429(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "slow down", http.StatusTooManyRequests)
	}))
	defer srv.Close()

	c := testBilibiliClient()
	c.PlayerEndpoint = srv.URL
	fallback := &fakeSource{transcript: &transcript.Transcript{
		Source:   transcript.SourceSubtitle,
		Provider: "yt-dlp",
		Segments: []transcript.Segment{{Text: "fallback"}},
	}}
	s := &BilibiliSource{
		Fallback: fallback,
		Native:   c,
		Guard:    URLGuard{AllowPrivateURLs: true, AllowUnlistedDomain: true},
	}
	got, err := s.Subtitle(context.Background(), &Meta{BVID: "BV1abcdefghi", CID: 123, URL: "https://www.bilibili.com/video/BV1abcdefghi"}, t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if got == nil || got.Provider != "yt-dlp" || fallback.subCalls != 1 {
		t.Fatalf("expected yt-dlp fallback, got %#v, calls=%d", got, fallback.subCalls)
	}
}

func TestBilibiliClientSubtitleSuccess(t *testing.T) {
	mux := http.NewServeMux()
	srv := httptest.NewServer(mux)
	defer srv.Close()

	mux.HandleFunc("/player", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("bvid") != "BV1abcdefghi" || r.URL.Query().Get("cid") != "123" {
			t.Fatalf("unexpected query: %s", r.URL.RawQuery)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"code":0,"data":{"subtitle":{"subtitles":[{"lan":"zh-Hans","subtitle_url":"` + srv.URL + `/subtitle","ai_type":0}]}}}`))
	})
	mux.HandleFunc("/subtitle", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"body":[{"from":1.25,"to":2.5,"content":"  第一行  "},{"from":2.5,"to":4,"content":"第二行"}]}`))
	})

	c := testBilibiliClient()
	c.PlayerEndpoint = srv.URL + "/player"
	c.AssetURLValidator = func(context.Context, string) error { return nil }
	got, err := c.Subtitle(context.Background(), &Meta{BVID: "BV1abcdefghi", CID: 123})
	if err != nil {
		t.Fatal(err)
	}
	if got == nil || got.Provider != "bilibili_player_api" || got.Language != "zh-Hans" {
		t.Fatalf("unexpected transcript: %#v", got)
	}
	if len(got.Segments) != 2 || got.Segments[0].StartMS != 1250 || got.Segments[0].EndMS != 2500 || got.Segments[0].Text != "第一行" {
		t.Fatalf("unexpected segments: %#v", got.Segments)
	}
}

func TestBilibiliClientMetadataSelectsPart(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("bvid") != "BV1abcdefghi" || r.URL.Query().Get("p") != "2" {
			t.Fatalf("unexpected query: %s", r.URL.RawQuery)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"code":0,"data":{"cid":1,"title":"主标题","desc":"desc","duration":100,"pic":"cover","owner":{"name":"up"},"pages":[{"cid":11,"duration":10,"part":"一"},{"cid":22,"duration":20,"part":"二"}]}}`))
	}))
	defer srv.Close()

	c := testBilibiliClient()
	c.ViewEndpoint = srv.URL
	got, err := c.Metadata(context.Background(), "https://www.bilibili.com/video/BV1abcdefghi?p=2")
	if err != nil {
		t.Fatal(err)
	}
	if got.CID != 22 || got.Part != 2 || got.DurationMS != 20000 || got.Title != "主标题 - P2 二" || got.Extractor != "bilibili-api" {
		t.Fatalf("unexpected metadata: %#v", got)
	}
}

func TestBilibiliSourceMetadataFallsBackOnAPI412(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"code":-412,"message":"risk"}`))
	}))
	defer srv.Close()

	c := testBilibiliClient()
	c.ViewEndpoint = srv.URL
	fallback := &fakeSource{meta: &Meta{
		Platform: "yt-dlp",
		VideoID:  "BV1abcdefghi",
		URL:      "https://www.bilibili.com/video/BV1abcdefghi",
		Title:    "fallback title",
	}}
	s := &BilibiliSource{
		Fallback: fallback,
		Native:   c,
		Guard:    URLGuard{AllowPrivateURLs: true, AllowUnlistedDomain: true},
	}
	got, err := s.Metadata(context.Background(), "https://www.bilibili.com/video/BV1abcdefghi")
	if err != nil {
		t.Fatal(err)
	}
	if got == nil || got.Title != "fallback title" || got.Platform != "bilibili" || fallback.metaCalls != 1 {
		t.Fatalf("expected metadata fallback, got %#v, calls=%d", got, fallback.metaCalls)
	}
}

func TestBilibiliClientDoesNotSendCookieToSubtitleCDN(t *testing.T) {
	c := testBilibiliClient()
	c.Cookie = "SESSDATA=secret"
	req := httptest.NewRequest(http.MethodGet, "https://aisubtitle.hdslb.com/subtitle.json", nil)
	c.setHeaders(req)
	if got := req.Header.Get("Cookie"); got != "" {
		t.Fatalf("cookie leaked to subtitle CDN: %q", got)
	}

	req = httptest.NewRequest(http.MethodGet, "https://api.bilibili.com/x/player/wbi/v2", nil)
	c.setHeaders(req)
	if got := req.Header.Get("Cookie"); got != "SESSDATA=secret" {
		t.Fatalf("expected cookie on bilibili API, got %q", got)
	}
}
