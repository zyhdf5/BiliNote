import json
import shutil
from pathlib import Path
from typing import Awaitable, Callable

from app.config.manager import ConfigManager
from app.llm.openai import OpenAICompatibleClient
from app.media.ffmpeg import normalize_for_asr
from app.models import SummaryResult, Transcript
from app.summary.summarizer import VideoSummarizer
from app.transcriber.whisper import FasterWhisperTranscriber
from app.utils.logging import append_task_log, redact_url
from app.video.registry import VideoSourceRegistry
from app.video.security import validate_source_policy, validate_resolved_target

Progress = Callable[[str, int], Awaitable[None]]


class VideoSummaryPipeline:
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        # The ASR runtime is intentionally immutable for the lifetime of the
        # process. Changing model/device/compute type requires restart.
        self._asr = FasterWhisperTranscriber(config_manager.get().asr)

    async def preload_asr(self) -> dict:
        return await self._asr.preload()

    def start_asr_preload(self) -> dict:
        return self._asr.start_preload()

    def asr_status(self) -> dict:
        return self._asr.status()

    async def run(self, url: str, task_dir: Path, progress: Progress) -> SummaryResult:
        cfg = self.config_manager.get()
        task_log = task_dir / "task.log"
        append_task_log(task_log, f"task pipeline started: {redact_url(url)}")
        source = VideoSourceRegistry(cfg).resolve(url)
        append_task_log(task_log, f"video source adapter selected: {source.name}")

        await progress("fetching_metadata", 5)
        meta = await source.metadata(url, task_log)

        # Re-validate the canonical URL returned by an extractor before it is
        # reused for subtitle/download stages. This does not replace network-level
        # egress filtering, but prevents the service itself from following a
        # canonical private/unlisted target in later stages.
        validate_source_policy(meta.url, cfg.video)
        await validate_resolved_target(meta.url, allow_private=cfg.video.allow_private_urls)

        if meta.duration and meta.duration > cfg.task.max_video_duration_seconds:
            raise ValueError(
                f"video duration {meta.duration}s exceeds configured limit "
                f"{cfg.task.max_video_duration_seconds}s"
            )

        transcript: Transcript | None = None
        await progress("fetching_subtitle", 15)
        try:
            transcript = await source.subtitle(meta, task_dir, task_log)
            if transcript:
                append_task_log(
                    task_log,
                    f"subtitle acquired: source={transcript.source} language={transcript.language or '-'}",
                )
            else:
                append_task_log(task_log, "no usable subtitle; ASR fallback will be used")
        except Exception as exc:
            append_task_log(task_log, f"subtitle fetch failed; ASR fallback will be used: {exc}")

        if transcript is None:
            if not cfg.asr.enabled:
                raise RuntimeError("video has no usable subtitle and ASR is disabled")
            usage = shutil.disk_usage(task_dir)
            if usage.free < int(cfg.task.min_free_disk_gb * 1024**3):
                raise RuntimeError(
                    f"insufficient disk space before download: {usage.free / 1024**3:.2f} GiB free; "
                    f"need at least {cfg.task.min_free_disk_gb:.2f} GiB"
                )
            await progress("downloading", 25)
            audio = await source.download_audio(meta, task_dir, task_log)
            await progress("transcoding", 45)
            normalized = await normalize_for_asr(
                audio,
                task_dir / "audio.wav",
                timeout=cfg.task.ffmpeg_timeout_seconds,
                log_path=task_log,
            )
            await progress("transcribing", 55)
            append_task_log(task_log, f"starting Faster-Whisper: {cfg.asr.model}/{cfg.asr.device}/{cfg.asr.compute_type}")
            transcript = await self._asr.transcribe(normalized)
            append_task_log(task_log, f"ASR completed with {len(transcript.segments)} segments")

        self._write_json(task_dir / "metadata.json", json.loads(meta.model_dump_json()))
        self._write_json(task_dir / "transcript.json", json.loads(transcript.model_dump_json()))

        await progress("summarizing", 75)
        llm = OpenAICompatibleClient(cfg.llm)
        summarizer = VideoSummarizer(cfg.summary, llm)
        summary_md, chunk_count = await summarizer.summarize(transcript, meta.title)
        result = SummaryResult(
            video=meta,
            transcript_source=transcript.source,
            transcript_language=transcript.language,
            summary_markdown=summary_md,
            chunk_count=chunk_count,
        )
        self._write_text(task_dir / "summary.md", summary_md)
        self._write_json(task_dir / "summary.json", json.loads(result.model_dump_json()))
        append_task_log(task_log, f"summary completed; chunks={chunk_count}")
        await progress("completed", 100)
        return result

    @staticmethod
    def _write_text(path: Path, text: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def _write_json(cls, path: Path, payload: dict) -> None:
        cls._write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
