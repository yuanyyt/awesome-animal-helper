"""Shared application dependencies and lifecycle management."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import FastAPI

from src.backend.agents.guide import GuideAgentService
from src.backend.integrations.amap.client import AmapClient
from src.backend.integrations.audio.realtime import AudioRealtimeService
from src.backend.knowledge import KnowledgeService
from src.backend.knowledge.service import KnowledgeBuildError
from src.backend.repositories.animals import AnimalRepository
from src.backend.repositories.wiki import WikiRepository

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_repository() -> AnimalRepository:
    """Return the process-wide immutable animal repository."""

    return AnimalRepository()


@lru_cache(maxsize=1)
def get_wiki_repository() -> WikiRepository:
    """Return a reload-on-change view of generated Wiki pages."""

    return WikiRepository()


@lru_cache(maxsize=1)
def get_amap_client() -> AmapClient:
    """Return the server-side AMap client without exposing credentials."""

    return AmapClient.from_env()


@lru_cache(maxsize=1)
def get_knowledge_service() -> KnowledgeService:
    """Return the persistent animal knowledge service."""

    return KnowledgeService()


@lru_cache(maxsize=1)
def get_guide_agent() -> GuideAgentService:
    """Build the guide lazily so basic endpoints do not require an LLM."""

    return GuideAgentService(
        get_amap_client(),
        get_repository(),
        get_knowledge_service(),
    )


@lru_cache(maxsize=1)
def get_audio_realtime() -> AudioRealtimeService:
    """Build the audio bridge only when a voice session starts."""

    return AudioRealtimeService()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Warm local data and release external clients on shutdown."""

    get_repository()
    get_wiki_repository()
    try:
        get_knowledge_service().ensure_ready()
    except KnowledgeBuildError:
        logger.exception("动物知识库初始化失败，动物讲解将降级为结构化资料")
    yield
    if get_amap_client.cache_info().currsize:
        get_amap_client().close()
