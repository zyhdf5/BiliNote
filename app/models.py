from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class Transcript(BaseModel):
    source: str
    language: str | None = None
    segments: list[TranscriptSegment]
    text: str
    raw: dict[str, Any] = Field(default_factory=dict)


class VideoMeta(BaseModel):
    platform: str = "generic"
    url: str
    video_id: str = ""
    # Bilibili-only compatibility fields. They stay optional for generic sources.
    bvid: str | None = None
    cid: int | None = None
    part: int | None = None
    title: str = ""
    author: str = ""
    duration: int = 0
    cover_url: str = ""
    extractor: str = ""


class SummaryResult(BaseModel):
    video: VideoMeta
    transcript_source: str
    transcript_language: str | None = None
    summary_markdown: str
    chunk_count: int


class CreateSummaryRequest(BaseModel):
    url: str = Field(min_length=10)
