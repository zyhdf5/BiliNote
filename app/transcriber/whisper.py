import asyncio
from pathlib import Path

from app.config.schema import ASRConfig
from app.models import Transcript, TranscriptSegment


class FasterWhisperTranscriber:
    def __init__(self, cfg: ASRConfig):
        self.cfg = cfg
        self._model = None
        self._model_lock = asyncio.Lock()
        self._model_load_task: asyncio.Task | None = None
        self._semaphore = asyncio.Semaphore(cfg.concurrency)

    def _load_model_sync(self):
        from faster_whisper import WhisperModel

        return WhisperModel(
            self.cfg.model,
            device=self.cfg.device,
            compute_type=self.cfg.compute_type,
            download_root=self.cfg.model_dir,
        )

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
        except Exception:
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
