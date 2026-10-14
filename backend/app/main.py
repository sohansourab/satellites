"""OrbitDesk backend application entry point.

Run from the repo root with:
    uvicorn backend.app.main:app --reload
(or from backend/: uvicorn app.main:app --reload)
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .config import settings
from .logging_conf import get_logger
from .routers import chat as chat_router
from .routers import satellites as satellites_router

log = get_logger("main")

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    if settings.force_demo_mode:
        log.info("Demo mode forced via configuration.")
    yield


app = FastAPI(
    title="OrbitDesk API",
    description="AI-powered space mission control dashboard backend.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(satellites_router.router)
app.include_router(chat_router.router)


@app.get("/api/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "demo_mode": settings.force_demo_mode,
        "ai_enabled": settings.ai_enabled,
        "cache_ttl_seconds": settings.cache_ttl_seconds,
    }


if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")
