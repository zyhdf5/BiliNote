from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.models import Transcript, VideoMeta


class VideoSource(Protocol):
    name: str

    def supports(self, url: str) -> bool: ...

    async def metadata(self, url: str, log_path: Path) -> VideoMeta: ...

    async def subtitle(self, meta: VideoMeta, task_dir: Path, log_path: Path) -> Transcript | None: ...

    async def download_audio(self, meta: VideoMeta, task_dir: Path, log_path: Path) -> Path: ...
