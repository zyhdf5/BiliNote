import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config.manager import ConfigManager
from app.config.schema import ASRConfig, AppConfig, VideoConfig
from app.transcriber.whisper import FasterWhisperTranscriber
from app.utils.logging import redact_text
from app.video.registry import BilibiliSource
from app.video.security import validate_source_policy
from app.web import render as render_module


def test_markdown_html_is_sanitized(monkeypatch):
    fake_markdown = SimpleNamespace(
        markdown=lambda *_args, **_kwargs: '<p>ok</p><script>alert(1)</script><a href="javascript:alert(2)" onclick="x()">bad</a>'
    )
    monkeypatch.setattr(render_module, "_markdown", fake_markdown)
    result = render_module.render_markdown_safe("ignored")
    assert "<p>ok</p>" in result
    assert "<script" not in result
    assert "javascript:" not in result
    assert "onclick" not in result


@pytest.mark.asyncio
async def test_model_load_cancellation_does_not_start_second_native_load():
    transcriber = FasterWhisperTranscriber(ASRConfig(device="cpu", compute_type="int8"))
    calls = 0
    model = object()

    def slow_load():
        nonlocal calls
        calls += 1
        time.sleep(0.15)
        return model

    transcriber._load_model_sync = slow_load
    first = asyncio.create_task(transcriber.preload())
    await asyncio.sleep(0.02)
    first.cancel()
    second = asyncio.create_task(transcriber.preload())

    with pytest.raises(asyncio.CancelledError):
        await first
    result = await second
    assert result["ok"] is True
    assert calls == 1
    assert transcriber._model is model


def test_disabled_asr_preload_does_not_start_background_work():
    transcriber = FasterWhisperTranscriber(ASRConfig(enabled=False, device="cpu", compute_type="int8"))
    result = transcriber.start_preload()
    assert result["state"] == "error"
    assert result["error"] == "ASR is disabled"
    assert transcriber._preload_task is None


@pytest.mark.asyncio
async def test_background_asr_preload_consumes_load_failure():
    transcriber = FasterWhisperTranscriber(ASRConfig(device="cpu", compute_type="int8"))

    def fail_load():
        raise RuntimeError("model unavailable")

    transcriber._load_model_sync = fail_load
    result = transcriber.start_preload()
    assert result["state"] == "starting"
    task = transcriber._preload_task
    assert task is not None
    await task
    assert transcriber.status()["state"] == "error"
    assert "model unavailable" in transcriber.status()["error"]


def test_asr_hot_update_is_rejected(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text("{}\n", encoding="utf-8")
    manager = ConfigManager(str(config))
    with pytest.raises(ValueError, match="restart-required"):
        manager.update_ui_settings({"asr": {"model": "tiny"}})


def test_default_source_allowlist_rejects_arbitrary_domain():
    cfg = VideoConfig()
    assert validate_source_policy("https://www.youtube.com/watch?v=abc", cfg)
    assert validate_source_policy("https://www.bilibili.com/video/BV1abcdefghij", cfg)
    with pytest.raises(ValueError, match="allowed_domains"):
        validate_source_policy("https://example.com/video", cfg)


def test_unlisted_domains_can_be_explicitly_enabled():
    cfg = VideoConfig(allow_unlisted_domains=True)
    assert validate_source_policy("https://example.com/video", cfg)


def test_process_log_text_redacts_embedded_credentials():
    text = redact_text(
        "download https://cdn.example/video?x=1&token=super-secret Authorization: Bearer abc SESSDATA=cookie-value"
    )
    assert "super-secret" not in text
    assert "Bearer abc" not in text
    assert "cookie-value" not in text
    assert "x=1" in text


@pytest.mark.asyncio
async def test_disabling_bilibili_native_subtitle_keeps_generic_fallback(tmp_path: Path):
    class FakeDownloader:
        async def fetch_subtitle(self, url, task_dir, log_path):
            return "fallback"

    cfg = AppConfig()
    cfg.bilibili.prefer_subtitle = False
    cfg.video.prefer_subtitle = True
    source = BilibiliSource(cfg, FakeDownloader())
    meta = SimpleNamespace(cid=123, url="https://www.bilibili.com/video/BV1abcdefghij")
    result = await source.subtitle(meta, tmp_path, tmp_path / "task.log")
    assert result == "fallback"


def test_video_url_rejects_userinfo_credentials():
    cfg = VideoConfig()
    with pytest.raises(ValueError, match="userinfo"):
        validate_source_policy("https://user:password@youtube.com/watch?v=abc", cfg)


@pytest.mark.asyncio
async def test_queue_capacity_is_enforced(tmp_path: Path):
    from app.task.manager import TaskCapacityError, TaskManager

    config = tmp_path / "config.yaml"
    config.write_text(
        f"""video:\n  allow_private_urls: true\n  allow_unlisted_domains: true\ntask:\n  max_queue_size: 1\n  work_dir: {tmp_path / 'tasks'}\n  db_path: {tmp_path / 'tasks.db'}\n  min_free_disk_gb: 0.1\n""",
        encoding="utf-8",
    )
    manager = TaskManager(ConfigManager(str(config)))
    await manager.create("http://127.0.0.1/video/one")
    with pytest.raises(TaskCapacityError, match="queue is full"):
        await manager.create("http://127.0.0.1/video/two")


def _request(headers: list[tuple[bytes, bytes]]):
    from starlette.requests import Request

    return Request({
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/system/test-gpu",
        "raw_path": b"/api/v1/system/test-gpu",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    })


def test_browser_api_write_requires_same_origin_and_csrf():
    from fastapi import HTTPException
    from app.security import verify_browser_api_write

    cross = _request([
        (b"host", b"testserver"),
        (b"origin", b"https://evil.example"),
        (b"cookie", b"bilinote_csrf=abc"),
        (b"x-csrf-token", b"abc"),
    ])
    with pytest.raises(HTTPException) as exc:
        verify_browser_api_write(cross)
    assert exc.value.status_code == 403

    missing_token = _request([
        (b"host", b"testserver"),
        (b"origin", b"http://testserver"),
        (b"cookie", b"bilinote_csrf=abc"),
    ])
    with pytest.raises(HTTPException) as exc:
        verify_browser_api_write(missing_token)
    assert exc.value.status_code == 403

    good = _request([
        (b"host", b"testserver"),
        (b"origin", b"http://testserver"),
        (b"cookie", b"bilinote_csrf=abc"),
        (b"x-csrf-token", b"abc"),
    ])
    verify_browser_api_write(good)
