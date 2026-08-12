"""FastAPI application entry point."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.backend.api.dependencies import lifespan
from src.backend.api.router import api_router

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"


def create_app() -> FastAPI:
    """Create and configure the backend application."""

    application = FastAPI(
        title="红山森林动物园导览 API",
        description="查询动物介绍、地图与智能导览信息。",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.include_router(api_router)
    if FRONTEND_DIST.is_dir():
        application.mount(
            "/",
            StaticFiles(directory=FRONTEND_DIST, html=True),
            name="frontend",
        )
    return application


app = create_app()
