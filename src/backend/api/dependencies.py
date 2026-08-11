"""Shared application dependencies and lifecycle management."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import FastAPI

from src.backend.agents.guide import GuideAgentService
from src.backend.integrations.amap.client import AmapClient
from src.backend.integrations.audio.realtime import AudioRealtimeService
from src.backend.repositories.animals import AnimalRepository


@lru_cache(maxsize=1)
def get_repository() -> AnimalRepository:
    """Return the process-wide immutable animal repository."""

    return AnimalRepository()


@lru_cache(maxsize=1)
def get_amap_client() -> AmapClient:
    """Return the server-side AMap client without exposing credentials."""

    return AmapClient.from_env()


@lru_cache(maxsize=1)
def get_guide_agent() -> GuideAgentService:
    """Build the guide lazily so basic endpoints do not require an LLM."""

    return GuideAgentService(get_amap_client(), get_repository())


@lru_cache(maxsize=1)
def get_audio_realtime() -> AudioRealtimeService:
    """Build the audio bridge only when a voice session starts."""

    return AudioRealtimeService()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Warm local data and release external clients on shutdown."""

    get_repository()
    yield
    if get_amap_client.cache_info().currsize:
        get_amap_client().close()
