"""FastAPI application entry point."""

import re
from pathlib import Path

from fastapi import FastAPI
from starlette.types import Scope
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from src.backend.api.dependencies import lifespan
from src.backend.api.router import api_router

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
HASHED_ANIMAL_IMAGE = re.compile(
    r"^animals/.+-[0-9a-f]{8}(?:-(?:320|640|1024))?\.(?:png|webp)$"
)


class FrontendStaticFiles(StaticFiles):
    """Serve fingerprinted animal images with a durable browser cache."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code in {200, 304} and HASHED_ANIMAL_IMAGE.fullmatch(path):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


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
            FrontendStaticFiles(directory=FRONTEND_DIST, html=True),
            name="frontend",
        )
    return application


app = create_app()
