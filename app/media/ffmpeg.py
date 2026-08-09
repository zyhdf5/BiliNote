from pathlib import Path

from app.utils.process import run_logged_process


async def normalize_for_asr(source: Path, target: Path, *, timeout: int, log_path: Path) -> Path:
    """Normalize to 16 kHz mono PCM and make ffmpeg cancellable/observable."""
    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-y",
        "-i", str(source),
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(target),
    ]
    await run_logged_process(cmd, timeout=timeout, log_path=log_path, label="ffmpeg")
    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError("ffmpeg completed but normalized audio is empty")
    return target
