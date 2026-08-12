import asyncio
import os
import threading
from pathlib import Path

import httpx

from app.config.schema import ASRConfig
from app.models import Transcript, TranscriptSegment

# Files required to construct a CTranslate2/faster-whisper model from a local
# directory. Downloaded directly (with resume) instead of going through the
# huggingface_hub cache, whose per-process temp files make concurrent/slow
# downloads restart from zero. Mirrors faster-whisper's own allow_patterns:
# preprocessor_config.json / vocabulary.* are optional and absent in some repos
# (e.g. faster-whisper-medium), in which case library defaults are used.
MODEL_REQUIRED = ["config.json", "model.bin", "tokenizer.json"]
MODEL_OPTIONAL = ["preprocessor_config.json", "vocabulary.txt", "vocabulary.json"]


class FasterWhisperTranscriber:
    def __init__(self, cfg: ASRConfig):
        self.cfg = cfg
        self._model = None
        self._model_lock = asyncio.Lock()
        self._model_load_task: asyncio.Task | None = None
        self._preload_task: asyncio.Task | None = None
        self._semaphore = asyncio.Semaphore(cfg.concurrency)
        # Written from the loader thread, read from async status callers.
        self._status_lock = threading.Lock()
        self._status: dict = {"state": "not_loaded"}

    # ----- status -----------------------------------------------------

    def status(self) -> dict:
        with self._status_lock:
            return dict(self._status)

    def _set_status(self, **fields) -> None:
        with self._status_lock:
            self._status.update(fields)

    # ----- model download / resolve (runs in the loader thread) -------

    def _resolve_model_path(self) -> Path:
        """Return a local directory containing all model files.

        cfg.model may already be a local directory; otherwise it is a model
        size name (e.g. "medium", "large-v3") and the files are downloaded
        from the HF endpoint mirror into <model_dir>/faster-whisper-<name>.
        """
        direct = Path(self.cfg.model)
        if direct.is_dir():
            return direct
        target = Path(self.cfg.model_dir) / f"faster-whisper-{self.cfg.model}"
        self._download_model(target)
        missing = [name for name in MODEL_REQUIRED if not (target / name).is_file()]
        if missing:
            raise RuntimeError(f"Whisper model files missing after download: {', '.join(missing)}")
        return target

    def _download_model(self, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        endpoint = os.getenv("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
        base = f"{endpoint}/Systran/faster-whisper-{self.cfg.model}/resolve/main"
        with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60.0, read=300.0)) as client:
            for name in MODEL_REQUIRED:
                self._sync_file(client, base, name, target / name, optional=False)
            for name in MODEL_OPTIONAL:
                self._sync_file(client, base, name, target / name, optional=True)

    def _sync_file(self, client: httpx.Client, base: str, name: str, dest: Path, *, optional: bool) -> None:
        url = f"{base}/{name}"
        head = client.head(url)
        if head.status_code == 404 and optional:
            return  # optional file simply does not exist in this repo
        head.raise_for_status()
        total = int(head.headers.get("content-length") or 0)
        existing = dest.stat().st_size if dest.exists() else 0
        if total and existing == total:
            return  # already fully downloaded
        if total and existing > total:
            dest.unlink()  # local file is corrupt/oversized; restart it
            existing = 0
        headers = {"Range": f"bytes={existing}-"} if existing else {}
        with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code == 416:
                return  # local file already covers the full range
            resp.raise_for_status()
            mode = "ab"
            if resp.status_code == 200:
                # Server ignored the Range request; restart the file.
                mode, existing = "wb", 0
            done = existing
            self._set_status(state="downloading", file=name, downloaded_bytes=done, total_bytes=total)
            with dest.open(mode) as fh:
                for chunk in resp.iter_bytes(1024 * 1024):
                    fh.write(chunk)
                    done += len(chunk)
                    self._set_status(state="downloading", file=name, downloaded_bytes=done, total_bytes=total)

    # ----- model load ---------------------------------------------------

    def _load_model_sync(self):
        from faster_whisper import WhisperModel

        model_path = self._resolve_model_path()
        self._set_status(state="loading", file=None)
        model = WhisperModel(
            str(model_path),
            device=self.cfg.device,
            compute_type=self.cfg.compute_type,
            download_root=self.cfg.model_dir,
        )
        self._set_status(state="loaded", model=str(model_path))
        return model

    async def _ensure_model(self):
        if self._model is not None:
            return self._model

        # Create exactly one native model-load operation. asyncio cancellation
        # cannot terminate the worker thread that constructs CTranslate2, so all
        # callers share the same task and cancellation waits for it to settle.
        async with self._model_lock:
            if self._model is not None:
                return self._model
            if self._model_load_task is None:
                self._model_load_task = asyncio.create_task(
                    asyncio.to_thread(self._load_model_sync),
                    name=f"whisper-model-load-{self.cfg.model}",
                )
            load_task = self._model_load_task

        try:
            model = await asyncio.shield(load_task)
        except asyncio.CancelledError:
            try:
                model = await load_task
                async with self._model_lock:
                    if self._model is None:
                        self._model = model
                    if self._model_load_task is load_task:
                        self._model_load_task = None
            finally:
                raise
        except Exception as exc:
            self._set_status(state="error", error=f"{type(exc).__name__}: {exc}")
            async with self._model_lock:
                if self._model_load_task is load_task:
                    self._model_load_task = None
            raise

        async with self._model_lock:
            if self._model is None:
                self._model = model
            if self._model_load_task is load_task:
                self._model_load_task = None
            return self._model

    def start_preload(self) -> dict:
        """Kick off an asynchronous download+load if not already loaded/loading."""
        if not self.cfg.enabled:
            self._set_status(state="error", error="ASR is disabled")
            return self.status()
        if self._model is None and (self._preload_task is None or self._preload_task.done()):
            self._set_status(state="starting", error=None, file=None)
            self._preload_task = asyncio.create_task(
                self._run_preload_background(),
                name=f"whisper-preload-{self.cfg.model}",
            )
        return self.status()

    async def _run_preload_background(self) -> None:
        current = asyncio.current_task()
        try:
            await self._ensure_model()
        except Exception:
            # _ensure_model records the exception in the public status. Consume
            # it here so a failed detached preload does not emit an unhandled
            # task exception warning.
            pass
        finally:
            if self._preload_task is current:
                self._preload_task = None

    async def preload(self) -> dict:
        if not self.cfg.enabled:
            return {"ok": False, "reason": "ASR disabled"}
        await self._ensure_model()
        return {
            "ok": True,
            "model": self.cfg.model,
            "device": self.cfg.device,
            "compute_type": self.cfg.compute_type,
        }

    async def transcribe(self, audio_path: Path) -> Transcript:
        if not self.cfg.enabled:
            raise RuntimeError("ASR is disabled and no subtitle is available")
        async with self._semaphore:
            model = await self._ensure_model()
            # CTranslate2 executes in a native worker thread. Cancelling the asyncio
            # wrapper cannot stop that native work. Keep the GPU semaphore occupied
            # until the native call really returns, otherwise a canceled task could
            # overlap with the next transcription and cause a GPU-memory spike.
            native_task = asyncio.create_task(
                asyncio.to_thread(self._transcribe_sync, model, audio_path),
                name=f"whisper-native-{audio_path.name}",
            )
            try:
                return await asyncio.shield(native_task)
            except asyncio.CancelledError:
                try:
                    await native_task
                finally:
                    raise

    def _transcribe_sync(self, model, audio_path: Path) -> Transcript:
        segments_raw, info = model.transcribe(
            str(audio_path),
            language=self.cfg.language or None,
            beam_size=self.cfg.beam_size,
            vad_filter=self.cfg.vad_filter,
        )
        segments = []
        for seg in segments_raw:
            text = (seg.text or "").strip()
            if text:
                segments.append(TranscriptSegment(start=float(seg.start), end=float(seg.end), text=text))
        if not segments:
            raise RuntimeError("Whisper returned an empty transcript")
        return Transcript(
            source="faster_whisper",
            language=getattr(info, "language", None),
            segments=segments,
            text=" ".join(s.text for s in segments),
            raw={"model": self.cfg.model, "device": self.cfg.device, "compute_type": self.cfg.compute_type},
        )
