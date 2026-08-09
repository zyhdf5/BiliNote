import uvicorn

from app.main import config_manager

if __name__ == "__main__":
    cfg = config_manager.get().server
    uvicorn.run("app.main:app", host=cfg.host, port=cfg.port, reload=False)
