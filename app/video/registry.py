from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from app.config.schema import AppConfig
from app.downloader.ytdlp import YtDlpDownloader
from app.models import Transcript, VideoMeta
from app.utils.logging import append_task_log
from app.video.bilibili.client import BilibiliClient


def is_bilibili_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "b23.tv" or host.endswith(".b23.tv") or host == "bilibili.com" or host.endswith(".bilibili.com")


class BilibiliSource:
    name = "bilibili"

    def __init__(self, cfg: AppConfig, downloader: YtDlpDownloader):
        self.cfg = cfg
        self.client = BilibiliClient(cfg.bilibili)
        self.downloader = downloader

    def supports(self, url: str) -> bool:
        return is_bilibili_url(url)

    async def metadata(self, url: str, log_path: Path) -> VideoMeta:
        try:
            meta = await self.client.metadata(url)
            append_task_log(log_path, "Bilibili metadata API succeeded")
            return meta
        except Exception as exc:
            append_task_log(log_path, f"Bilibili metadata failed; falling back to yt-dlp: {exc}")
            meta = await self.downloader.probe_metadata(url, log_path)
            meta.platform = "bilibili"
            return meta

    async def subtitle(self, meta: VideoMeta, task_dir: Path, log_path: Path) -> Transcript | None:
        if self.cfg.bilibili.prefer_subtitle and meta.cid:
            try:
                transcript = await self.client.subtitle(meta)
                if transcript:
                    return transcript
            except Exception as exc:
                append_task_log(log_path, f"Bilibili native subtitle failed: {exc}")
        # Disabling the dedicated Bilibili API must not disable the generic
        # subtitle path. video.prefer_subtitle controls the yt-dlp fallback.
        if self.cfg.video.prefer_subtitle:
            return await self.downloader.fetch_subtitle(meta.url, task_dir, log_path)
        return None

    async def download_audio(self, meta: VideoMeta, task_dir: Path, log_path: Path) -> Path:
        return await self.downloader.download_audio(meta.url, task_dir, log_path)


class GenericYtDlpSource:
    name = "yt-dlp"

    def __init__(self, cfg: AppConfig, downloader: YtDlpDownloader):
        self.cfg = cfg
        self.downloader = downloader

    def supports(self, url: str) -> bool:
        return True

    async def metadata(self, url: str, log_path: Path) -> VideoMeta:
        meta = await self.downloader.probe_metadata(url, log_path)
        append_task_log(log_path, f"yt-dlp source resolved: platform={meta.platform} extractor={meta.extractor or '-'}")
        return meta

    async def subtitle(self, meta: VideoMeta, task_dir: Path, log_path: Path) -> Transcript | None:
        if not self.cfg.video.prefer_subtitle:
            return None
        return await self.downloader.fetch_subtitle(meta.url, task_dir, log_path)

    async def download_audio(self, meta: VideoMeta, task_dir: Path, log_path: Path) -> Path:
        return await self.downloader.download_audio(meta.url, task_dir, log_path)


class VideoSourceRegistry:
    def __init__(self, cfg: AppConfig):
        downloader = YtDlpDownloader(cfg.video, cfg.bilibili, cfg.task)
        # Ordering matters: dedicated adapters before the generic fallback.
        self.sources = [
            BilibiliSource(cfg, downloader),
            GenericYtDlpSource(cfg, downloader),
        ]

    def resolve(self, url: str):
        for source in self.sources:
            if source.supports(url):
                return source
        raise ValueError("unsupported video source")
