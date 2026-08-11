"""FastAPI application entry point."""

from fastapi import FastAPI

from src.backend.api.dependencies import lifespan
from src.backend.api.router import api_router


def create_app() -> FastAPI:
    """Create and configure the backend application."""

    application = FastAPI(
        title="红山森林动物园导览 API",
        description="查询动物介绍、地图与智能导览信息。",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.include_router(api_router)
    return application


app = create_app()
