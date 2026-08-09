import asyncio
import json
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config.manager import ConfigManager
from app.models import utcnow_iso
from app.pipeline.video_summary import VideoSummaryPipeline
from app.utils.logging import append_task_log, redact_text, tail_text
from app.video.security import validate_http_url, validate_resolved_target, validate_source_policy
from .repository import TERMINAL_STATUSES, TaskRepository


class TaskCapacityError(RuntimeError):
    pass


class TaskManager:
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        cfg = config_manager.get().task
        self.repo = TaskRepository(cfg.db_path)
        self.repo.recover_incomplete()
        self.pipeline = VideoSummaryPipeline(config_manager)
        self.queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=cfg.max_queue_size)
        self.workers: list[asyncio.Task] = []
        self.cleanup_task: asyncio.Task | None = None
        self.active: dict[str, asyncio.Task] = {}

    async def start(self):
        count = self.config_manager.get().task.concurrency
        self.workers = [asyncio.create_task(self._worker(i), name=f"summary-worker-{i}") for i in range(count)]
        self.cleanup_task = asyncio.create_task(self._cleanup_loop(), name="task-cleanup")

    async def stop(self):
        if self.cleanup_task:
            self.cleanup_task.cancel()
        for task in list(self.active.values()):
            task.cancel()
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*(list(self.active.values()) + self.workers + ([self.cleanup_task] if self.cleanup_task else [])), return_exceptions=True)
        self.active.clear()
        self.workers.clear()
        self.cleanup_task = None

    @staticmethod
    def _validate_url(url: str, allow_private: bool = False) -> str:
        return validate_http_url(url, allow_private=allow_private)

    async def create(self, url: str) -> dict:
        cfg = self.config_manager.get()
        allow_private = cfg.video.allow_private_urls
        url = validate_source_policy(url, cfg.video)
        url = await validate_resolved_target(url, allow_private=allow_private)

        work_dir = Path(cfg.task.work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(work_dir)
        if usage.free < int(cfg.task.min_free_disk_gb * 1024**3):
            raise TaskCapacityError(
                f"insufficient disk space: {usage.free / 1024**3:.2f} GiB free; "
                f"need at least {cfg.task.min_free_disk_gb:.2f} GiB"
            )
        if self.queue.full():
            raise TaskCapacityError(f"task queue is full (max {self.queue.maxsize})")

        task_id = uuid.uuid4().hex
        self.repo.create(task_id, url)
        try:
            self.queue.put_nowait((task_id, url))
        except asyncio.QueueFull as exc:
            self.repo.delete(task_id)
            raise TaskCapacityError(f"task queue is full (max {self.queue.maxsize})") from exc
        return self.get(task_id)

    def get(self, task_id: str) -> dict | None:
        item = self.repo.get(task_id)
        if not item:
            return None
        if item.get("result_path"):
            path = Path(item["result_path"])
            if path.exists():
                try:
                    item["result"] = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    item["result"] = None
        log_path = Path(self.config_manager.get().task.work_dir) / task_id / "task.log"
        item["log_tail"] = tail_text(log_path, 24 * 1024)
        return item

    def list(self, limit: int = 100) -> list[dict]:
        return self.repo.list(limit)

    async def cancel(self, task_id: str) -> bool:
        item = self.repo.get(task_id)
        if not item or item["status"] in TERMINAL_STATUSES:
            return False
        active = self.active.get(task_id)
        if active:
            self.repo.update(
                task_id,
                stage="canceling",
                error="task cancellation requested; waiting for the current native operation to stop safely",
            )
            append_task_log(self._task_log(task_id), "task cancellation requested")
            active.cancel()
        else:
            self.repo.update(
                task_id,
                status="canceled",
                stage="canceled",
                error="task canceled before execution",
                finished_at=utcnow_iso(),
            )
        return True

    async def delete(self, task_id: str) -> bool:
        item = self.repo.get(task_id)
        if not item:
            return False
        if item["status"] not in TERMINAL_STATUSES:
            await self.cancel(task_id)
            active = self.active.get(task_id)
            if active:
                await asyncio.gather(active, return_exceptions=True)
        work_dir = Path(self.config_manager.get().task.work_dir) / task_id
        shutil.rmtree(work_dir, ignore_errors=True)
        self.repo.delete(task_id)
        return True

    async def _worker(self, index: int):
        while True:
            task_id, url = await self.queue.get()
            try:
                current = self.repo.get(task_id)
                if not current or current["status"] == "canceled":
                    continue
                run_task = asyncio.create_task(self._run_one(task_id, url), name=f"video-summary-{task_id[:8]}")
                self.active[task_id] = run_task
                try:
                    await run_task
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                    self.repo.update(
                        task_id,
                        status="canceled",
                        stage="canceled",
                        error="task canceled by user",
                        finished_at=utcnow_iso(),
                    )
                    append_task_log(self._task_log(task_id), "task canceled by user")
                finally:
                    self.active.pop(task_id, None)
            finally:
                self.queue.task_done()

    async def _run_one(self, task_id: str, url: str):
        cfg = self.config_manager.get().task
        task_dir = Path(cfg.work_dir) / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        append_task_log(task_dir / "task.log", "worker accepted task")
        self.repo.update(task_id, status="running", stage="fetching_metadata", started_at=utcnow_iso())

        async def progress(stage: str, percent: int):
            status = "succeeded" if stage == "completed" else "running"
            self.repo.update(task_id, status=status, stage=stage, progress=percent)
            append_task_log(task_dir / "task.log", f"stage={stage} progress={percent}%")

        try:
            result = await asyncio.wait_for(
                self.pipeline.run(url, task_dir, progress),
                timeout=cfg.task_timeout_seconds,
            )
            self.repo.update(
                task_id,
                title=result.video.title,
                platform=result.video.platform,
                transcript_source=result.transcript_source,
                result_path=str(task_dir / "summary.json"),
                status="succeeded",
                stage="completed",
                progress=100,
                finished_at=utcnow_iso(),
                error=None,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            message = f"task timed out after {cfg.task_timeout_seconds}s"
            append_task_log(task_dir / "task.log", message)
            self.repo.update(task_id, status="failed", stage="failed", error=message, finished_at=utcnow_iso())
        except Exception as exc:
            append_task_log(task_dir / "task.log", f"task failed: {type(exc).__name__}: {exc}")
            self.repo.update(
                task_id,
                status="failed",
                stage="failed",
                error=redact_text(f"{type(exc).__name__}: {exc}")[:8000],
                finished_at=utcnow_iso(),
            )

    def _task_log(self, task_id: str) -> Path:
        return Path(self.config_manager.get().task.work_dir) / task_id / "task.log"

    async def _cleanup_loop(self):
        while True:
            try:
                await self.cleanup_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(self.config_manager.get().task.cleanup_interval_seconds)

    async def cleanup_once(self) -> int:
        cfg = self.config_manager.get().task
        cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.retain_days)
        items = self.repo.list_finished_before(cutoff.isoformat())
        count = 0
        for item in items:
            if item["id"] in self.active:
                continue
            shutil.rmtree(Path(cfg.work_dir) / item["id"], ignore_errors=True)
            self.repo.delete(item["id"])
            count += 1
        return count
