"""FastAPI application exposing local animal guide data."""

from contextlib import asynccontextmanager
from functools import lru_cache
from typing import AsyncIterator

from fastapi import FastAPI, Query

from .repository import AnimalRepository
from .schemas import AnimalListResponse


@lru_cache(maxsize=1)
def get_repository() -> AnimalRepository:
    """Build the repository once per backend process."""

    return AnimalRepository()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Validate and cache source data before accepting requests."""

    get_repository()
    yield


app = FastAPI(
    title="红山森林动物园导览 API",
    description="查询动物介绍及其所在场馆。",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/api/animals", response_model=AnimalListResponse)
async def list_animals(
    q: str | None = Query(default=None, max_length=100),
    site: str | None = Query(default=None, max_length=50),
    name: str | None = Query(default=None, max_length=50),
) -> AnimalListResponse:
    """List, search, or locate animals through one stable endpoint."""

    return get_repository().query(q=q, site=site, name=name)
