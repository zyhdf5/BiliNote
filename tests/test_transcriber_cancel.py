import asyncio
import time
from pathlib import Path

import pytest

from app.config.schema import ASRConfig
from app.models import Transcript, TranscriptSegment
from app.transcriber.whisper import FasterWhisperTranscriber


@pytest.mark.asyncio
async def test_native_transcription_keeps_gpu_slot_until_thread_returns(tmp_path: Path):
    tr = FasterWhisperTranscriber(ASRConfig(device="cpu", compute_type="int8", concurrency=1))
    tr._model = object()

    def fake_sync(model, path):
        time.sleep(0.2)
        return Transcript(
            source="test",
            language="zh",
            segments=[TranscriptSegment(start=0, end=1, text="ok")],
            text="ok",
        )

    tr._transcribe_sync = fake_sync
    started = time.monotonic()
    task = asyncio.create_task(tr.transcribe(tmp_path / "audio.wav"))
    await asyncio.sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Cancellation must not release the semaphore while the native thread is
    # still executing.
    assert time.monotonic() - started >= 0.18
