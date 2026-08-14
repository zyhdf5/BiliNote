package config

import (
	"fmt"
	"os"
	"regexp"
	"strconv"
	"time"

	"gopkg.in/yaml.v3"
)

type Config struct {
	Server    ServerConfig    `yaml:"server"`
	Postgres  PostgresConfig  `yaml:"postgres"`
	Workspace WorkspaceConfig `yaml:"workspace"`
	Worker    WorkerConfig    `yaml:"worker"`
	Video     VideoConfig     `yaml:"video"`
	Bilibili  BilibiliConfig  `yaml:"bilibili"`
	YTDLP     YTDLPConfig     `yaml:"ytdlp"`
	FFmpeg    FFmpegConfig    `yaml:"ffmpeg"`
	ASR       ASRConfig       `yaml:"asr"`
	LLM       LLMConfig       `yaml:"llm"`
	Summary   SummaryConfig   `yaml:"summary"`
}

type ServerConfig struct {
	Addr            string        `yaml:"addr"`
	ShutdownTimeout time.Duration `yaml:"-"`
	ShutdownRaw     string        `yaml:"shutdown_timeout"`
}

type PostgresConfig struct {
	DSN string `yaml:"dsn"`
}

type WorkspaceConfig struct {
	Root          string        `yaml:"root"`
	StaleAfter    time.Duration `yaml:"-"`
	StaleAfterRaw string        `yaml:"stale_after"`
}

type WorkerConfig struct {
	Concurrency     int           `yaml:"concurrency"`
	PollInterval    time.Duration `yaml:"-"`
	PollIntervalRaw string        `yaml:"poll_interval"`
	LeaseDuration   time.Duration `yaml:"-"`
	LeaseRaw        string        `yaml:"lease_duration"`
	MaxAttempts     int           `yaml:"max_attempts"`
}

type VideoConfig struct {
	PreferSubtitle      bool     `yaml:"prefer_subtitle"`
	SubtitleLanguages   []string `yaml:"subtitle_languages"`
	CookiesFile         string   `yaml:"cookies_file"`
	Proxy               string   `yaml:"proxy"`
	AllowPrivateURLs    bool     `yaml:"allow_private_urls"`
	AllowUnlistedDomain bool     `yaml:"allow_unlisted_domains"`
	AllowedDomains      []string `yaml:"allowed_domains"`
}

type BilibiliConfig struct {
	Cookie            string        `yaml:"cookie"`
	RequestTimeout    time.Duration `yaml:"-"`
	RequestTimeoutRaw string        `yaml:"request_timeout"`
	Retries           int           `yaml:"retries"`
	RetryBackoff      time.Duration `yaml:"-"`
	RetryBackoffRaw   string        `yaml:"retry_backoff"`
}

type YTDLPConfig struct {
	Binary          string        `yaml:"binary"`
	Timeout         time.Duration `yaml:"-"`
	TimeoutRaw      string        `yaml:"timeout"`
	Retries         int           `yaml:"retries"`
	FragmentRetries int           `yaml:"fragment_retries"`
	Concurrency     int           `yaml:"concurrency"`
}

type FFmpegConfig struct {
	Binary     string        `yaml:"binary"`
	Timeout    time.Duration `yaml:"-"`
	TimeoutRaw string        `yaml:"timeout"`
}

type ASRConfig struct {
	Enabled     bool          `yaml:"enabled"`
	BaseURL     string        `yaml:"base_url"`
	APIKey      string        `yaml:"api_key"`
	Model       string        `yaml:"model"`
	Language    string        `yaml:"language"`
	Timeout     time.Duration `yaml:"-"`
	TimeoutRaw  string        `yaml:"timeout"`
	MaxRetries  int           `yaml:"max_retries"`
	Concurrency int           `yaml:"concurrency"`
}

type LLMConfig struct {
	BaseURL     string        `yaml:"base_url"`
	APIKey      string        `yaml:"api_key"`
	Model       string        `yaml:"model"`
	Temperature float64       `yaml:"temperature"`
	MaxTokens   int           `yaml:"max_tokens"`
	Timeout     time.Duration `yaml:"-"`
	TimeoutRaw  string        `yaml:"timeout"`
	MaxRetries  int           `yaml:"max_retries"`
}

type SummaryConfig struct {
	ChunkChars     int  `yaml:"chunk_chars"`
	MapConcurrency int  `yaml:"map_concurrency"`
	KeepTranscript bool `yaml:"keep_transcript"`
}

var envPattern = regexp.MustCompile(`\$\{([A-Za-z_][A-Za-z0-9_]*)\}`)

func Load(path string) (*Config, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	expanded := envPattern.ReplaceAllStringFunc(string(b), func(s string) string {
		m := envPattern.FindStringSubmatch(s)
		return os.Getenv(m[1])
	})
	var c Config
	if err := yaml.Unmarshal([]byte(expanded), &c); err != nil {
		return nil, err
	}
	if err := c.parseDurations(); err != nil {
		return nil, err
	}
	if c.Server.Addr == "" {
		c.Server.Addr = ":8080"
	}
	if c.Workspace.Root == "" {
		c.Workspace.Root = "/tmp/bilinote"
	}
	if c.Worker.Concurrency <= 0 {
		c.Worker.Concurrency = 1
	}
	if c.Worker.MaxAttempts <= 0 {
		c.Worker.MaxAttempts = 3
	}
	if c.Bilibili.Retries <= 0 {
		c.Bilibili.Retries = 2
	}
	if c.Summary.ChunkChars <= 0 {
		c.Summary.ChunkChars = 30000
	}
	if c.Summary.MapConcurrency <= 0 {
		c.Summary.MapConcurrency = 4
	}
	return &c, nil
}

func (c *Config) parseDurations() error {
	var err error
	if c.Server.ShutdownTimeout, err = duration(c.Server.ShutdownRaw, 15*time.Second); err != nil {
		return fmt.Errorf("server.shutdown_timeout: %w", err)
	}
	if c.Workspace.StaleAfter, err = duration(c.Workspace.StaleAfterRaw, 24*time.Hour); err != nil {
		return fmt.Errorf("workspace.stale_after: %w", err)
	}
	if c.Worker.PollInterval, err = duration(c.Worker.PollIntervalRaw, 2*time.Second); err != nil {
		return fmt.Errorf("worker.poll_interval: %w", err)
	}
	if c.Worker.LeaseDuration, err = duration(c.Worker.LeaseRaw, 2*time.Minute); err != nil {
		return fmt.Errorf("worker.lease_duration: %w", err)
	}
	if c.Bilibili.RequestTimeout, err = duration(c.Bilibili.RequestTimeoutRaw, 20*time.Second); err != nil {
		return fmt.Errorf("bilibili.request_timeout: %w", err)
	}
	if c.Bilibili.RetryBackoff, err = duration(c.Bilibili.RetryBackoffRaw, 500*time.Millisecond); err != nil {
		return fmt.Errorf("bilibili.retry_backoff: %w", err)
	}
	if c.YTDLP.Timeout, err = duration(c.YTDLP.TimeoutRaw, 30*time.Minute); err != nil {
		return fmt.Errorf("ytdlp.timeout: %w", err)
	}
	if c.FFmpeg.Timeout, err = duration(c.FFmpeg.TimeoutRaw, 15*time.Minute); err != nil {
		return fmt.Errorf("ffmpeg.timeout: %w", err)
	}
	if c.ASR.Timeout, err = duration(c.ASR.TimeoutRaw, 30*time.Minute); err != nil {
		return fmt.Errorf("asr.timeout: %w", err)
	}
	if c.LLM.Timeout, err = duration(c.LLM.TimeoutRaw, 10*time.Minute); err != nil {
		return fmt.Errorf("llm.timeout: %w", err)
	}
	return nil
}

func duration(v string, def time.Duration) (time.Duration, error) {
	if v == "" {
		return def, nil
	}
	if n, err := strconv.Atoi(v); err == nil {
		return time.Duration(n) * time.Second, nil
	}
	return time.ParseDuration(v)
}
