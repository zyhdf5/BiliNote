from pydantic import BaseModel, Field


_DEFAULT_VIDEO_DOMAINS = [
    "bilibili.com",
    "b23.tv",
    "youtube.com",
    "youtu.be",
    "douyin.com",
    "iesdouyin.com",
    "kuaishou.com",
    "tiktok.com",
]


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080


class SecurityConfig(BaseModel):
    web_username: str = ""
    web_password: str = ""


class VideoConfig(BaseModel):
    """Cross-platform source policy and generic yt-dlp settings."""

    prefer_subtitle: bool = True
    subtitle_languages: list[str] = Field(default_factory=lambda: ["zh-Hans", "zh-Hant", "zh.*", "en.*"])
    cookies_file: str = ""
    request_timeout_seconds: int = Field(default=20, ge=3, le=120)
    allow_private_urls: bool = False
    allow_unlisted_domains: bool = False
    allowed_domains: list[str] = Field(default_factory=lambda: list(_DEFAULT_VIDEO_DOMAINS))


class BilibiliConfig(BaseModel):
    prefer_subtitle: bool = True
    cookie: str = ""
    request_timeout_seconds: int = Field(default=15, ge=3, le=120)
    retries: int = Field(default=3, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=1.0, ge=0.1, le=10)


class ASRConfig(BaseModel):
    enabled: bool = True
    model: str = "large-v3"
    device: str = "cuda"
    compute_type: str = "float16"
    language: str | None = "zh"
    beam_size: int = Field(default=5, ge=1, le=20)
    vad_filter: bool = True
    model_dir: str = "/models"
    concurrency: int = Field(default=1, ge=1, le=8)
    preload_model: bool = False


class LLMConfig(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=4096, ge=16)
    timeout_seconds: int = Field(default=300, ge=5, le=3600)
    retries: int = Field(default=2, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=1.0, ge=0.1, le=30)


class SummaryConfig(BaseModel):
    prompt_file: str = "/app/prompts/summary.md"
    chunk_chars: int = Field(default=24000, ge=2000)
    parallel: int = Field(default=3, ge=1, le=16)


class TaskConfig(BaseModel):
    concurrency: int = Field(default=2, ge=1, le=16)
    max_queue_size: int = Field(default=20, ge=1, le=1000)
    retain_days: int = Field(default=7, ge=1)
    cleanup_interval_seconds: int = Field(default=3600, ge=60)
    work_dir: str = "/data/tasks"
    db_path: str = "/data/tasks.db"
    download_timeout_seconds: int = Field(default=1800, ge=30)
    ffmpeg_timeout_seconds: int = Field(default=600, ge=30)
    # This is a soft wall-clock timeout. Native Whisper inference is allowed to
    # finish safely before its GPU slot is released; see HARDENING.md.
    task_timeout_seconds: int = Field(default=7200, ge=60)
    ytdlp_retries: int = Field(default=5, ge=0, le=50)
    ytdlp_fragment_retries: int = Field(default=5, ge=0, le=50)
    max_video_duration_seconds: int = Field(default=14400, ge=60)
    max_download_mb: int = Field(default=2048, ge=32)
    min_free_disk_gb: float = Field(default=2.0, ge=0.1)


class SystemConfig(BaseModel):
    startup_check: bool = True
    fail_startup_on_error: bool = False
    readiness_cache_seconds: int = Field(default=30, ge=1, le=300)


class AppConfig(BaseModel):
    server: ServerConfig = ServerConfig()
    security: SecurityConfig = SecurityConfig()
    video: VideoConfig = VideoConfig()
    bilibili: BilibiliConfig = BilibiliConfig()
    asr: ASRConfig = ASRConfig()
    llm: LLMConfig = LLMConfig()
    summary: SummaryConfig = SummaryConfig()
    task: TaskConfig = TaskConfig()
    system: SystemConfig = SystemConfig()
