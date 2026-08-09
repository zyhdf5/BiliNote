import asyncio
import shutil
import subprocess
import sys
import time
from pathlib import Path

from app.config.manager import ConfigManager


class SystemChecker:
    def __init__(self, config_manager: ConfigManager, pipeline=None):
        self.config_manager = config_manager
        self.pipeline = pipeline
        self.last_result: dict = {"ok": False, "checks": {}}
        self._last_checked_monotonic = 0.0
        self._lock = asyncio.Lock()

    async def current(self, *, max_age_seconds: int) -> dict:
        if self._last_checked_monotonic and time.monotonic() - self._last_checked_monotonic <= max_age_seconds:
            return self.last_result
        return await self.run()

    async def run(self, *, preload_asr: bool = False) -> dict:
        async with self._lock:
            cfg = self.config_manager.get()
            checks: dict[str, dict] = {}
            checks["python"] = self._python()
            checks["yt_dlp"] = await asyncio.to_thread(self._ytdlp)
            checks["ffmpeg"] = self._binary("ffmpeg")
            checks["data_dir"] = self._dir(cfg.task.work_dir, cfg.task.min_free_disk_gb)
            checks["web_auth"] = self._web_auth(cfg.security.web_username, cfg.security.web_password)
            if cfg.video.cookies_file:
                checks["cookies_file"] = self._file(cfg.video.cookies_file)
            if cfg.asr.enabled and cfg.asr.device.lower().startswith("cuda"):
                checks["gpu"] = await asyncio.to_thread(self._gpu)
                checks["ctranslate2"] = await asyncio.to_thread(self._ctranslate2)
            else:
                checks["gpu"] = {"ok": True, "skipped": True, "reason": "ASR is not configured for CUDA"}
                checks["ctranslate2"] = {"ok": True, "skipped": True}
            if preload_asr and cfg.asr.enabled:
                try:
                    if not self.pipeline:
                        raise RuntimeError("pipeline is unavailable")
                    checks["asr_model"] = await self.pipeline.preload_asr()
                except Exception as exc:
                    checks["asr_model"] = {"ok": False, "reason": str(exc)}
            ok = all(bool(v.get("ok")) for v in checks.values())
            self.last_result = {"ok": ok, "checks": checks}
            self._last_checked_monotonic = time.monotonic()
            return self.last_result

    @staticmethod
    def _python() -> dict:
        version = tuple(sys.version_info[:3])
        return {
            "ok": version >= (3, 11, 0),
            "version": ".".join(map(str, version)),
            "reason": "" if version >= (3, 11, 0) else "Python 3.11+ is required for the maintained yt-dlp runtime",
        }

    @staticmethod
    def _ytdlp() -> dict:
        path = shutil.which("yt-dlp")
        if not path:
            return {"ok": False, "path": "", "reason": "yt-dlp not found"}
        try:
            result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=8, check=True)
            version = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
            parts = version.replace("-", ".").split(".")[:3]
            parsed = tuple(int(x) for x in parts) if len(parts) == 3 else (0, 0, 0)
            minimum = (2026, 7, 4)
            return {
                "ok": parsed >= minimum,
                "path": path,
                "version": version,
                "reason": "" if parsed >= minimum else "yt-dlp 2026.07.04+ is required; rebuild/upgrade the image",
            }
        except Exception as exc:
            return {"ok": False, "path": path, "reason": str(exc)}

    @staticmethod
    def _binary(name: str) -> dict:
        path = shutil.which(name)
        return {"ok": bool(path), "path": path or "", "reason": "" if path else f"{name} not found"}

    @staticmethod
    def _dir(path: str, min_free_gb: float) -> dict:
        try:
            p = Path(path)
            p.mkdir(parents=True, exist_ok=True)
            probe = p / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            usage = shutil.disk_usage(p)
            free_gb = usage.free / 1024**3
            return {
                "ok": free_gb >= min_free_gb,
                "free_gb": round(free_gb, 2),
                "reason": "" if free_gb >= min_free_gb else f"less than {min_free_gb:.2f} GiB free",
            }
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}

    @staticmethod
    def _web_auth(username: str, password: str) -> dict:
        enabled = bool(username and password)
        return {
            "ok": True,
            "enabled": enabled,
            "reason": "" if enabled else "disabled; Docker Compose binds 8080 to host loopback by default",
        }

    @staticmethod
    def _file(path: str) -> dict:
        try:
            p = Path(path)
            if not p.is_file():
                return {"ok": False, "path": str(p), "reason": "configured cookies file does not exist"}
            with p.open("rb") as f:
                f.read(1)
            return {"ok": True, "path": str(p)}
        except Exception as exc:
            return {"ok": False, "path": path, "reason": str(exc)}

    @staticmethod
    def _gpu() -> dict:
        if not shutil.which("nvidia-smi"):
            return {"ok": False, "reason": "nvidia-smi not found; check Docker GPU passthrough/NVIDIA Container Toolkit"}
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=8,
                check=True,
            )
            rows = [x.strip() for x in result.stdout.splitlines() if x.strip()]
            return {"ok": bool(rows), "gpus": rows, "reason": "" if rows else "no GPU returned by nvidia-smi"}
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}

    @staticmethod
    def _ctranslate2() -> dict:
        try:
            import ctranslate2
            count = int(ctranslate2.get_cuda_device_count())
            return {"ok": count > 0, "cuda_device_count": count, "version": getattr(ctranslate2, "__version__", "")}
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}
