package video

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/zyhdf5/bilinote-go/internal/transcript"
)

const bilibiliUserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36"

var (
	bvidRE                    = regexp.MustCompile(`BV[0-9A-Za-z]{10}`)
	ErrBilibiliRiskControl    = errors.New("bilibili risk control")
	ErrBilibiliUnsafeRedirect = errors.New("bilibili unsafe redirect")
)

type BilibiliOptions struct {
	Cookie       string
	Timeout      time.Duration
	Retries      int
	RetryBackoff time.Duration
}

type BilibiliClient struct {
	Cookie       string
	Retries      int
	RetryBackoff time.Duration
	HTTP         *http.Client
	Guard        URLGuard

	ViewEndpoint   string
	PlayerEndpoint string

	// AssetURLValidator exists to make the player subtitle download path testable.
	// NewBilibiliClient installs the production validator.
	AssetURLValidator func(context.Context, string) error
}

func NewBilibiliClient(guard URLGuard, opts BilibiliOptions) *BilibiliClient {
	timeout := opts.Timeout
	if timeout <= 0 {
		timeout = 20 * time.Second
	}
	backoff := opts.RetryBackoff
	if backoff <= 0 {
		backoff = 500 * time.Millisecond
	}
	c := &BilibiliClient{
		Cookie:         strings.TrimSpace(opts.Cookie),
		Retries:        max(0, opts.Retries),
		RetryBackoff:   backoff,
		HTTP:           &http.Client{Timeout: timeout},
		Guard:          guard,
		ViewEndpoint:   "https://api.bilibili.com/x/web-interface/view",
		PlayerEndpoint: "https://api.bilibili.com/x/player/wbi/v2",
	}
	c.AssetURLValidator = c.validateAssetURL
	return c
}

func (c *BilibiliClient) Metadata(ctx context.Context, inputURL string) (*Meta, error) {
	resolved, err := c.resolveURL(ctx, inputURL)
	if err != nil {
		return nil, err
	}
	match := bvidRE.FindString(resolved)
	if match == "" {
		return nil, fmt.Errorf("bilibili: cannot find BV id in URL")
	}
	part := bilibiliPartNumber(resolved)
	params := url.Values{"bvid": []string{match}}
	if part > 0 {
		params.Set("p", strconv.Itoa(part))
	}
	body, err := c.get(ctx, c.ViewEndpoint, params)
	if err != nil {
		return nil, err
	}
	var payload struct {
		Code    int    `json:"code"`
		Message string `json:"message"`
		Data    struct {
			CID      int64  `json:"cid"`
			Title    string `json:"title"`
			Desc     string `json:"desc"`
			Duration int64  `json:"duration"`
			Pic      string `json:"pic"`
			Owner    struct {
				Name string `json:"name"`
			} `json:"owner"`
			Pages []struct {
				CID      int64  `json:"cid"`
				Duration int64  `json:"duration"`
				Part     string `json:"part"`
			} `json:"pages"`
		} `json:"data"`
	}
	if err := json.Unmarshal(body, &payload); err != nil {
		return nil, fmt.Errorf("decode bilibili view response: %w", err)
	}
	if payload.Code == -412 || payload.Code == -352 {
		return nil, fmt.Errorf("%w: api code %d: %s", ErrBilibiliRiskControl, payload.Code, payload.Message)
	}
	if payload.Code != 0 {
		return nil, fmt.Errorf("bilibili view api code %d: %s", payload.Code, payload.Message)
	}

	cid := payload.Data.CID
	duration := payload.Data.Duration
	title := payload.Data.Title
	if len(payload.Data.Pages) > 0 {
		idx := 0
		if part > 0 {
			idx = part - 1
		}
		if idx < 0 || idx >= len(payload.Data.Pages) {
			idx = 0
		}
		selected := payload.Data.Pages[idx]
		if selected.CID > 0 {
			cid = selected.CID
		}
		if selected.Duration > 0 {
			duration = selected.Duration
		}
		if selected.Part != "" && len(payload.Data.Pages) > 1 {
			selectedPart := idx + 1
			title = fmt.Sprintf("%s - P%d %s", title, selectedPart, selected.Part)
			if part == 0 {
				part = selectedPart
			}
		}
	}

	return &Meta{
		Platform:    "bilibili",
		VideoID:     match,
		URL:         resolved,
		Title:       title,
		Description: payload.Data.Desc,
		Author:      payload.Data.Owner.Name,
		DurationMS:  duration * 1000,
		BVID:        match,
		CID:         cid,
		Part:        part,
		CoverURL:    payload.Data.Pic,
		Extractor:   "bilibili-api",
	}, nil
}

func (c *BilibiliClient) Subtitle(ctx context.Context, meta *Meta) (*transcript.Transcript, error) {
	if meta == nil || meta.BVID == "" || meta.CID <= 0 {
		return nil, nil
	}
	params := url.Values{
		"bvid": []string{meta.BVID},
		"cid":  []string{strconv.FormatInt(meta.CID, 10)},
	}
	body, err := c.get(ctx, c.PlayerEndpoint, params)
	if err != nil {
		return nil, err
	}
	var payload struct {
		Code    int    `json:"code"`
		Message string `json:"message"`
		Data    struct {
			Subtitle struct {
				Subtitles []bilibiliSubtitleTrack `json:"subtitles"`
			} `json:"subtitle"`
		} `json:"data"`
	}
	if err := json.Unmarshal(body, &payload); err != nil {
		return nil, fmt.Errorf("decode bilibili player response: %w", err)
	}
	if payload.Code == -412 || payload.Code == -352 {
		return nil, fmt.Errorf("%w: subtitle api code %d: %s", ErrBilibiliRiskControl, payload.Code, payload.Message)
	}
	if payload.Code != 0 || len(payload.Data.Subtitle.Subtitles) == 0 {
		return nil, nil
	}
	track := pickBilibiliTrack(payload.Data.Subtitle.Subtitles)
	if track == nil || strings.TrimSpace(track.SubtitleURL) == "" {
		return nil, nil
	}
	subtitleURL := strings.TrimSpace(track.SubtitleURL)
	if strings.HasPrefix(subtitleURL, "//") {
		subtitleURL = "https:" + subtitleURL
	}
	if c.AssetURLValidator != nil {
		if err := c.AssetURLValidator(ctx, subtitleURL); err != nil {
			return nil, err
		}
	}
	subBody, err := c.get(ctx, subtitleURL, nil)
	if err != nil {
		return nil, err
	}
	var doc struct {
		Body []struct {
			From    float64 `json:"from"`
			To      float64 `json:"to"`
			Content string  `json:"content"`
		} `json:"body"`
	}
	if err := json.Unmarshal(subBody, &doc); err != nil {
		return nil, fmt.Errorf("decode bilibili subtitle body: %w", err)
	}
	segments := make([]transcript.Segment, 0, len(doc.Body))
	for _, item := range doc.Body {
		text := strings.TrimSpace(item.Content)
		if text == "" {
			continue
		}
		segments = append(segments, transcript.Segment{
			StartMS: int64(math.Round(item.From * 1000)),
			EndMS:   int64(math.Round(item.To * 1000)),
			Text:    text,
		})
	}
	if len(segments) == 0 {
		return nil, nil
	}
	language := strings.TrimSpace(track.Language)
	if language == "" {
		language = "zh"
	}
	return &transcript.Transcript{
		Language: language,
		Source:   transcript.SourceSubtitle,
		Provider: "bilibili_player_api",
		Segments: segments,
	}, nil
}

type bilibiliSubtitleTrack struct {
	Language    string `json:"lan"`
	SubtitleURL string `json:"subtitle_url"`
	AIType      int    `json:"ai_type"`
}

func pickBilibiliTrack(tracks []bilibiliSubtitleTrack) *bilibiliSubtitleTrack {
	isChinese := func(t bilibiliSubtitleTrack) bool {
		lan := strings.ToLower(strings.TrimSpace(t.Language))
		return strings.HasPrefix(lan, "zh") || lan == "ai-zh"
	}
	for i := range tracks {
		if isChinese(tracks[i]) && tracks[i].AIType == 0 {
			return &tracks[i]
		}
	}
	for i := range tracks {
		if isChinese(tracks[i]) {
			return &tracks[i]
		}
	}
	if len(tracks) > 0 {
		return &tracks[0]
	}
	return nil
}

func (c *BilibiliClient) get(ctx context.Context, endpoint string, params url.Values) ([]byte, error) {
	if params != nil && len(params) > 0 {
		u, err := url.Parse(endpoint)
		if err != nil {
			return nil, err
		}
		q := u.Query()
		for k, values := range params {
			for _, value := range values {
				q.Add(k, value)
			}
		}
		u.RawQuery = q.Encode()
		endpoint = u.String()
	}

	var lastErr error
	for attempt := 0; attempt <= c.Retries; attempt++ {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
		if err != nil {
			return nil, err
		}
		c.setHeaders(req)
		resp, err := c.HTTP.Do(req)
		if err != nil {
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			lastErr = err
		} else {
			body, readErr := io.ReadAll(io.LimitReader(resp.Body, 16<<20))
			_ = resp.Body.Close()
			if readErr != nil {
				lastErr = readErr
			} else {
				switch {
				case resp.StatusCode == http.StatusPreconditionFailed || resp.StatusCode == http.StatusTooManyRequests:
					lastErr = fmt.Errorf("%w: http %d", ErrBilibiliRiskControl, resp.StatusCode)
				case resp.StatusCode >= 500:
					lastErr = fmt.Errorf("bilibili http %d", resp.StatusCode)
				case resp.StatusCode >= 400:
					return nil, fmt.Errorf("bilibili http %d: %s", resp.StatusCode, limitedText(body, 512))
				default:
					return body, nil
				}
			}
		}
		if attempt < c.Retries {
			if err := sleepContext(ctx, c.RetryBackoff*time.Duration(1<<attempt)); err != nil {
				return nil, err
			}
		}
	}
	if lastErr == nil {
		lastErr = errors.New("unknown bilibili request failure")
	}
	return nil, lastErr
}

func (c *BilibiliClient) resolveURL(ctx context.Context, rawURL string) (string, error) {
	u, err := url.Parse(rawURL)
	if err != nil {
		return "", err
	}
	if !strings.EqualFold(u.Hostname(), "b23.tv") && !strings.HasSuffix(strings.ToLower(u.Hostname()), ".b23.tv") {
		return rawURL, nil
	}

	current := rawURL
	noRedirect := *c.HTTP
	noRedirect.CheckRedirect = func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }
	for hop := 0; hop < 5; hop++ {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, current, nil)
		if err != nil {
			return "", err
		}
		c.setHeaders(req)
		resp, err := noRedirect.Do(req)
		if err != nil {
			return "", err
		}
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 64<<10))
		_ = resp.Body.Close()
		if resp.StatusCode == http.StatusPreconditionFailed || resp.StatusCode == http.StatusTooManyRequests {
			return "", fmt.Errorf("%w: b23.tv http %d", ErrBilibiliRiskControl, resp.StatusCode)
		}
		if resp.StatusCode >= 300 && resp.StatusCode < 400 {
			location := resp.Header.Get("Location")
			if location == "" {
				return "", fmt.Errorf("b23.tv redirect has no Location header")
			}
			base, _ := url.Parse(current)
			next, err := base.Parse(location)
			if err != nil {
				return "", err
			}
			if !isBilibiliHost(next.Hostname()) {
				return "", fmt.Errorf("%w: b23.tv redirected to unexpected host %q", ErrBilibiliUnsafeRedirect, next.Hostname())
			}
			if err := c.Guard.Validate(ctx, next.String()); err != nil {
				return "", fmt.Errorf("%w: %v", ErrBilibiliUnsafeRedirect, err)
			}
			current = next.String()
			continue
		}
		if resp.StatusCode >= 400 {
			return "", fmt.Errorf("b23.tv http %d", resp.StatusCode)
		}
		return current, nil
	}
	return "", fmt.Errorf("b23.tv too many redirects")
}

func (c *BilibiliClient) validateAssetURL(ctx context.Context, rawURL string) error {
	u, err := url.Parse(rawURL)
	if err != nil || (u.Scheme != "https" && u.Scheme != "http") || u.Hostname() == "" {
		return fmt.Errorf("invalid bilibili subtitle url")
	}
	host := strings.ToLower(strings.TrimSuffix(u.Hostname(), "."))
	if !(host == "hdslb.com" || strings.HasSuffix(host, ".hdslb.com") ||
		host == "bilivideo.com" || strings.HasSuffix(host, ".bilivideo.com") ||
		host == "bilibili.com" || strings.HasSuffix(host, ".bilibili.com")) {
		return fmt.Errorf("unexpected bilibili subtitle host %q", host)
	}
	guard := URLGuard{AllowPrivateURLs: c.Guard.AllowPrivateURLs, AllowUnlistedDomain: true, Resolver: c.Guard.Resolver}
	return guard.Validate(ctx, rawURL)
}

func (c *BilibiliClient) setHeaders(req *http.Request) {
	req.Header.Set("User-Agent", bilibiliUserAgent)
	req.Header.Set("Referer", "https://www.bilibili.com/")
	req.Header.Set("Accept", "application/json,text/plain,*/*")
	req.Header.Set("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.7")
	if c.Cookie != "" && isBilibiliHost(req.URL.Hostname()) {
		req.Header.Set("Cookie", c.Cookie)
	}
}

func bilibiliPartNumber(rawURL string) int {
	u, err := url.Parse(rawURL)
	if err != nil {
		return 0
	}
	p, err := strconv.Atoi(u.Query().Get("p"))
	if err != nil || p <= 0 {
		return 0
	}
	return p
}

func sleepContext(ctx context.Context, d time.Duration) error {
	if d <= 0 {
		return nil
	}
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}

func limitedText(b []byte, n int) string {
	s := strings.TrimSpace(string(b))
	if len(s) > n {
		return s[:n]
	}
	return s
}
