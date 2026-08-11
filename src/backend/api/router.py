"""Top-level backend router."""

from fastapi import APIRouter

from src.backend.api.routes import animals, guide, map, voice, wiki

api_router = APIRouter()
api_router.include_router(animals.router)
api_router.include_router(guide.router)
api_router.include_router(map.router)
api_router.include_router(voice.router)
api_router.include_router(wiki.router)
