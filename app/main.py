from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api.auth import DashboardAuthMiddleware
from app.api.dashboard import router as dashboard_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.webhooks import router as webhooks_router
from app.config import get_settings
from app.logging import configure_logging

_STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.agent_workspace.mkdir(parents=True, exist_ok=True)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Devbot", version="0.1.0", lifespan=lifespan)
    app.include_router(dashboard_router)
    app.include_router(health_router)
    app.include_router(jobs_router)
    app.include_router(webhooks_router)
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    settings = get_settings()
    app.add_middleware(DashboardAuthMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret(),
        session_cookie="devbot_session",
        same_site="lax",
        https_only=settings.session_https_only(),
        max_age=14 * 24 * 3600,
    )
    return app


app = create_app()
