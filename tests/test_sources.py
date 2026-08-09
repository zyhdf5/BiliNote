from pathlib import Path

import pytest

from app.config.schema import AppConfig
from app.downloader.ytdlp import parse_vtt
from app.task.manager import TaskManager
from app.video.registry import BilibiliSource, GenericYtDlpSource, VideoSourceRegistry
from app.video.security import validate_http_url


def test_url_validation_accepts_public_video_sources():
    assert TaskManager._validate_url("https://www.bilibili.com/video/BV1abcdefghij")
    assert TaskManager._validate_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert TaskManager._validate_url("https://v.douyin.com/example/")
    assert TaskManager._validate_url("https://www.kuaishou.com/short-video/example")


def test_url_validation_rejects_private_targets():
    with pytest.raises(ValueError):
        validate_http_url("http://127.0.0.1/video")
    with pytest.raises(ValueError):
        validate_http_url("http://10.0.0.1/video")
    with pytest.raises(ValueError):
        validate_http_url("http://localhost/video")
    with pytest.raises(ValueError):
        validate_http_url("file:///etc/passwd")


def test_source_registry_prefers_bilibili_adapter():
    cfg = AppConfig()
    registry = VideoSourceRegistry(cfg)
    assert isinstance(registry.resolve("https://www.bilibili.com/video/BV1abcdefghij"), BilibiliSource)
    assert isinstance(registry.resolve("https://www.youtube.com/watch?v=abc"), GenericYtDlpSource)


def test_parse_vtt(tmp_path: Path):
    path = tmp_path / "subtitle.zh-Hans.vtt"
    path.write_text(
        """WEBVTT\n\n00:00:01.000 --> 00:00:03.500\n你好 <c>世界</c>\n\n00:00:03.500 --> 00:00:05.000\n第二句\n""",
        encoding="utf-8",
    )
    transcript = parse_vtt(path, source="yt-dlp_subtitle", language="zh-Hans")
    assert transcript is not None
    assert transcript.language == "zh-Hans"
    assert transcript.segments[0].text == "你好 世界"
    assert transcript.segments[0].start == 1.0
    assert transcript.segments[1].end == 5.0

@pytest.mark.asyncio
async def test_generic_subtitle_prefers_manual_then_auto(tmp_path: Path, monkeypatch):
    from app.config.schema import BilibiliConfig, TaskConfig, VideoConfig
    from app.downloader import ytdlp as module
    from app.downloader.ytdlp import YtDlpDownloader

    calls = []

    async def fake_run(cmd, *, timeout, log_path, label, **kwargs):
        calls.append(label)
        if "--write-auto-subs" in cmd:
            out = Path(cmd[cmd.index("-o") + 1].replace("%(ext)s", "zh-Hans.vtt"))
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nauto caption\n",
                encoding="utf-8",
            )
        return ""

    monkeypatch.setattr(module, "run_logged_process", fake_run)
    downloader = YtDlpDownloader(VideoConfig(), BilibiliConfig(), TaskConfig())
    transcript = await downloader.fetch_subtitle(
        "https://www.youtube.com/watch?v=demo",
        tmp_path,
        tmp_path / "task.log",
    )
    assert calls == ["yt-dlp-subtitle", "yt-dlp-auto-subtitle"]
    assert transcript is not None
    assert transcript.source == "yt-dlp_auto_subtitle"
    assert transcript.text == "auto caption"
