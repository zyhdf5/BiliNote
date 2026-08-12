from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security import verify_csrf_form
from app.task.manager import TaskCapacityError
from app.web.render import render_markdown_safe

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    tasks = request.app.state.task_manager.list(10)
    return templates.TemplateResponse("index.html", {"request": request, "tasks": tasks})


@router.post("/submit")
async def submit(request: Request, url: str = Form(...), csrf_token: str = Form(...)):
    verify_csrf_form(request, csrf_token)
    try:
        task = await request.app.state.task_manager.create(url.strip())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except TaskCapacityError as exc:
        raise HTTPException(503, str(exc)) from exc
    return RedirectResponse(f"/tasks/{task['id']}", status_code=303)


@router.post("/tasks/{task_id}/cancel")
async def cancel_task_page(task_id: str, request: Request, csrf_token: str = Form(...)):
    verify_csrf_form(request, csrf_token)
    await request.app.state.task_manager.cancel(task_id)
    # 取消的任务会被直接删除，回到列表页。
    return RedirectResponse("/tasks", status_code=303)


@router.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request):
    return templates.TemplateResponse("tasks.html", {"request": request, "tasks": request.app.state.task_manager.list(200)})


@router.get("/tasks/{task_id}", response_class=HTMLResponse)
async def task_page(task_id: str, request: Request):
    task = request.app.state.task_manager.get(task_id)
    if not task:
        raise HTTPException(404, "task not found")
    result = task.get("result") or {}
    summary_html = render_markdown_safe(result.get("summary_markdown", ""))
    return templates.TemplateResponse(
        "task.html", {"request": request, "task": task, "summary_html": summary_html}
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    cfg = request.app.state.config_manager.public_dict()
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "cfg": cfg, "saved": False, "system": request.app.state.system_status},
    )


@router.post("/settings", response_class=HTMLResponse)
async def save_settings(
    request: Request,
    csrf_token: str = Form(...),
    llm_base_url: str = Form(""),
    llm_api_key: str = Form(""),
    llm_model: str = Form(""),
    temperature: float = Form(0.2),
    bilibili_cookie: str = Form(""),
    prefer_subtitle: str | None = Form(None),
    generic_prefer_subtitle: str | None = Form(None),
    generic_cookies_file: str = Form(""),
):
    verify_csrf_form(request, csrf_token)
    manager = request.app.state.config_manager
    manager.update_ui_settings({
        "llm": {"base_url": llm_base_url, "api_key": llm_api_key, "model": llm_model, "temperature": temperature},
        "video": {"prefer_subtitle": generic_prefer_subtitle == "on", "cookies_file": generic_cookies_file},
        "bilibili": {"cookie": bilibili_cookie, "prefer_subtitle": prefer_subtitle == "on"},
    })
    request.app.state.system_status = await request.app.state.system_checker.run()
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "cfg": manager.public_dict(), "saved": True, "system": request.app.state.system_status},
    )
