import asyncio
from pathlib import Path

import pytest

from app.config.schema import AppConfig
from app.task.manager import TaskManager
from app.utils.process import ProcessExecutionError, run_logged_process


def test_hardened_defaults():
    cfg = AppConfig()
    assert cfg.task.task_timeout_seconds >= cfg.task.download_timeout_seconds
    assert cfg.bilibili.retries >= 1
    assert cfg.llm.retries >= 1
    assert cfg.system.startup_check is True


def test_video_url_validation():
    assert TaskManager._validate_url("https://www.bilibili.com/video/BV1abcdefghij")
    assert TaskManager._validate_url("https://b23.tv/abc123")
    assert TaskManager._validate_url("https://example.com/video/123")
    with pytest.raises(ValueError):
        TaskManager._validate_url("http://127.0.0.1/private")
    with pytest.raises(ValueError):
        TaskManager._validate_url("file:///etc/passwd")


@pytest.mark.asyncio
async def test_process_timeout_and_log(tmp_path: Path):
    log = tmp_path / "task.log"
    with pytest.raises(ProcessExecutionError, match="timed out"):
        await run_logged_process(
            ["python3", "-c", "import time; print('started', flush=True); time.sleep(3)"],
            timeout=0.2,
            log_path=log,
            label="test",
        )
    text = log.read_text(encoding="utf-8")
    assert "started" in text


@pytest.mark.asyncio
async def test_process_capture_without_stdout_log(tmp_path: Path):
    log = tmp_path / "task.log"
    out = await run_logged_process(
        ["python3", "-c", "print('secret-ish-json')"],
        timeout=2,
        log_path=log,
        label="probe",
        capture_stdout=True,
        log_stdout=False,
    )
    assert out == "secret-ish-json"
    assert "[probe:stdout] secret-ish-json" not in log.read_text(encoding="utf-8")

@pytest.mark.asyncio
async def test_process_cancellation_terminates_child(tmp_path: Path):
    log = tmp_path / "task.log"
    task = asyncio.create_task(
        run_logged_process(
            ["python3", "-c", "import time; print('running', flush=True); time.sleep(10)"],
            timeout=30,
            log_path=log,
            label="cancel-test",
        )
    )
    await asyncio.sleep(0.15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "cancellation requested" in log.read_text(encoding="utf-8")


def test_command_log_redacts_sensitive_query(tmp_path: Path):
    from app.utils.logging import redact_url

    value = redact_url("https://example.com/watch?v=abc&token=secret&signature=sig")
    assert "v=abc" in value
    assert "secret" not in value
    assert "sig" not in value.split("signature=", 1)[-1]
