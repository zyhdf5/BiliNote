import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.config.manager import ConfigManager
from app.security import CSRFCookieMiddleware, OptionalBasicAuthMiddleware, SecurityHeadersMiddleware
from app.system.checks import SystemChecker
from app.task.manager import TaskManager
from app.web.routes import router as web_router

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("bilinote-summary")

CONFIG_PATH = os.getenv("APP_CONFIG", "/app/config.yaml")
if not Path(CONFIG_PATH).exists() and Path("config.yaml").exists():
    CONFIG_PATH = "config.yaml"

# Non-Docker runs (IDE, plain `python run.py`) do not inherit the PATH prepared
# by scripts/start_local.sh. Make the project-local portable FFmpeg always
# discoverable so yt-dlp postprocessing and the ASR pipeline work regardless
# of how the process was launched. On Windows, CTranslate2 loads cuBLAS/cuDNN
# from PATH as well, so include the pip-installed nvidia-* runtime DLL dirs.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXTRA_PATH_DIRS = [
    _REPO_ROOT / "tools" / "ffmpeg" / "bin",
    _REPO_ROOT / ".venv" / "Lib" / "site-packages" / "nvidia" / "cublas" / "bin",
    _REPO_ROOT / ".venv" / "Lib" / "site-packages" / "nvidia" / "cudnn" / "bin",
]
for _dir in reversed([d for d in _EXTRA_PATH_DIRS if d.is_dir()]):
    os.environ["PATH"] = str(_dir) + os.pathsep + os.environ["PATH"]

config_manager = ConfigManager(CONFIG_PATH)
task_manager = TaskManager(config_manager)
system_checker = SystemChecker(config_manager, task_manager.pipeline)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = config_manager.get()
    if cfg.system.startup_check:
        status = await system_checker.run(preload_asr=cfg.asr.preload_model)
        app.state.system_status = status
        if not status["ok"]:
            logger.warning("startup checks failed: %s", status)
            if cfg.system.fail_startup_on_error:
                raise RuntimeError(f"startup checks failed: {status}")
    else:
        app.state.system_status = {"ok": True, "checks": {}, "skipped": True}
    await task_manager.start()
    try:
        yield
    finally:
        await task_manager.stop()


app = FastAPI(title="BiliNote Summary", version="0.4.0", lifespan=lifespan)
app.state.config_manager = config_manager
app.state.task_manager = task_manager
app.state.system_checker = system_checker
app.state.system_status = {"ok": False, "checks": {}, "reason": "startup checks not run yet"}
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CSRFCookieMiddleware)
app.add_middleware(OptionalBasicAuthMiddleware, config_manager=config_manager)
app.include_router(api_router)
app.include_router(web_router)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "web" / "static")), name="static")


@app.get("/healthz")
async def healthz():
    return {
        "ok": True,
        "queue_size": task_manager.queue.qsize(),
        "queue_capacity": task_manager.queue.maxsize,
        "active_tasks": len(task_manager.active),
    }


@app.get("/readyz")
async def readyz():
    cfg = config_manager.get().system
    status = await system_checker.current(max_age_seconds=cfg.readiness_cache_seconds)
    app.state.system_status = status
    code = 200 if status.get("ok") else 503
    return JSONResponse(status_code=code, content=status)


@app.exception_handler(Exception)
async def unhandled_exception(_, exc: Exception):
    logger.exception("unhandled request error")
    return JSONResponse(status_code=500, content={"detail": "internal server error"})
