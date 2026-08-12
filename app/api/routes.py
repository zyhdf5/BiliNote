from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.llm.openai import OpenAICompatibleClient
from app.models import CreateSummaryRequest
from app.security import verify_browser_api_write
from app.task.manager import TaskCapacityError
from app.utils.logging import tail_text

router = APIRouter(prefix="/api/v1")


def services(request: Request):
    return request.app.state.config_manager, request.app.state.task_manager


@router.post("/summaries", status_code=202)
async def create_summary(body: CreateSummaryRequest, request: Request):
    verify_browser_api_write(request)
    _, task_manager = services(request)
    try:
        return await task_manager.create(body.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except TaskCapacityError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/tasks")
async def list_tasks(request: Request, limit: int = 100):
    _, task_manager = services(request)
    return {"items": task_manager.list(min(max(limit, 1), 500))}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, request: Request):
    _, task_manager = services(request)
    task = task_manager.get(task_id)
    if not task:
        raise HTTPException(404, "task not found")
    return task


@router.get("/tasks/{task_id}/log", response_class=PlainTextResponse)
async def get_task_log(task_id: str, request: Request):
    config_manager, task_manager = services(request)
    if not task_manager.repo.get(task_id):
        raise HTTPException(404, "task not found")
    path = Path(config_manager.get().task.work_dir) / task_id / "task.log"
    return PlainTextResponse(tail_text(path, 128 * 1024))


@router.post("/tasks/{task_id}/cancel", status_code=202)
async def cancel_task(task_id: str, request: Request):
    verify_browser_api_write(request)
    _, task_manager = services(request)
    if not await task_manager.cancel(task_id):
        raise HTTPException(409, "task is not cancellable or does not exist")
    return {"ok": True}


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(task_id: str, request: Request):
    verify_browser_api_write(request)
    _, task_manager = services(request)
    if not await task_manager.delete(task_id):
        raise HTTPException(404, "task not found")


@router.get("/settings")
async def get_settings(request: Request):
    config_manager, _ = services(request)
    return config_manager.public_dict()


@router.put("/settings")
async def update_settings(payload: dict, request: Request):
    verify_browser_api_write(request)
    config_manager, _ = services(request)
    try:
        config_manager.update_ui_settings(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    request.app.state.system_status = await request.app.state.system_checker.run()
    return config_manager.public_dict()


@router.post("/settings/test-llm")
async def test_llm(request: Request):
    verify_browser_api_write(request)
    config_manager, _ = services(request)
    return await OpenAICompatibleClient(config_manager.get().llm).health()


@router.post("/system/test-gpu")
async def test_gpu(request: Request):
    verify_browser_api_write(request)
    result = await request.app.state.system_checker.run()
    request.app.state.system_status = result
    return {"ok": result.get("checks", {}).get("gpu", {}).get("ok", False), "gpu": result.get("checks", {}).get("gpu", {})}


@router.post("/system/test-asr")
async def test_asr(request: Request):
    """Start an asynchronous Whisper download+load; poll /system/asr-status."""
    verify_browser_api_write(request)
    pipeline = request.app.state.task_manager.pipeline
    return pipeline.start_asr_preload()


@router.get("/system/asr-status")
async def asr_status(request: Request):
    return request.app.state.task_manager.pipeline.asr_status()


@router.post("/system/cleanup")
async def cleanup(request: Request):
    verify_browser_api_write(request)
    _, task_manager = services(request)
    count = await task_manager.cleanup_once()
    return {"ok": True, "deleted": count}
