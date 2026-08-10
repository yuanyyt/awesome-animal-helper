"""FastAPI application exposing local animal guide data."""

from contextlib import asynccontextmanager
from functools import lru_cache
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket

from .amap_client import AmapClient, AmapServiceError, amap_proxy_target
from .audio_realtime import AudioRealtimeError, AudioRealtimeService
from .guide_agent import GuideAgentError, GuideAgentService
from .repository import AnimalRepository
from .schemas import (
    AnimalListResponse,
    GuideChatRequest,
    GuideChatResponse,
    GuideContinueRequest,
    MapGuideResponse,
)


@lru_cache(maxsize=1)
def get_repository() -> AnimalRepository:
    """Build the repository once per backend process."""

    return AnimalRepository()


@lru_cache(maxsize=1)
def get_amap_client() -> AmapClient:
    """Build the server-side AMap client without exposing its key."""

    return AmapClient.from_env()


@lru_cache(maxsize=1)
def get_guide_agent() -> GuideAgentService:
    """Build the Agno guide lazily so animal and map endpoints remain independent."""

    return GuideAgentService(get_amap_client(), get_repository())


@lru_cache(maxsize=1)
def get_audio_realtime() -> AudioRealtimeService:
    """Build the Qwen-Audio bridge only when a voice session starts."""

    return AudioRealtimeService(get_guide_agent())


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Validate and cache source data before accepting requests."""

    get_repository()
    yield
    if get_amap_client.cache_info().currsize:
        get_amap_client().close()


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


@app.post("/api/guide/chat", response_model=GuideChatResponse)
async def chat_with_guide(request: GuideChatRequest) -> GuideChatResponse:
    """Start one conversational route-planning turn."""

    try:
        return await get_guide_agent().chat(
            request.message,
            request.session_id,
            request.map_context,
        )
    except GuideAgentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="导览员暂时无法回答，请稍后重试") from exc


@app.post("/api/guide/chat/{run_id}/continue", response_model=GuideChatResponse)
async def continue_guide_chat(
    run_id: str,
    request: GuideContinueRequest,
) -> GuideChatResponse:
    """Resolve the current Agno HITL fields and resume a paused run."""

    try:
        return await get_guide_agent().continue_run(
            run_id,
            request.session_id,
            request.values,
        )
    except GuideAgentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="导览会话暂时无法继续，请稍后重试") from exc


@app.websocket("/api/guide/voice")
async def realtime_voice_guide(websocket: WebSocket) -> None:
    """Bridge browser PCM audio to Qwen-Audio without exposing credentials."""

    try:
        service = get_audio_realtime()
    except (AudioRealtimeError, ValueError) as exc:
        await websocket.accept()
        await websocket.send_json({"type": "error", "message": str(exc)})
        await websocket.close(code=1011)
        return
    await service.serve(websocket)


@app.get("/api/map", response_model=MapGuideResponse)
def get_map_guide() -> MapGuideResponse:
    """Return AMap-backed venue coordinates and safe map configuration."""

    try:
        return get_amap_client().build_guide(get_repository().site_summaries())
    except (AmapServiceError, httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="地图服务暂不可用，请稍后重试") from exc


@app.get("/api/map/image", response_class=Response)
def get_map_image() -> Response:
    """Proxy the AMap static map so the Web Service key stays server-side."""

    try:
        client = get_amap_client()
        guide = client.build_guide(get_repository().site_summaries())
        content, media_type = client.static_map(guide.center)
        return Response(
            content=content,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=3600"},
        )
    except (AmapServiceError, httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="地图图片暂不可用，请稍后重试") from exc


@app.api_route(
    "/_AMapService/{path:path}",
    methods=["GET", "POST"],
    response_class=Response,
)
async def proxy_amap_js_service(path: str, request: Request) -> Response:
    """Proxy allow-listed AMap JS API services and append the secret jscode."""
    amap = get_amap_client()
    if not amap.js_api_enabled:
        raise HTTPException(status_code=503, detail="高德 JS API 安全代理未配置")
    try:
        target = amap_proxy_target(path)
        params = [
            (key, value)
            for key, value in request.query_params.multi_items()
            if key.casefold() != "jscode"
        ]
        params.append(("jscode", amap.security_key))
        headers = {}
        if content_type := request.headers.get("content-type"):
            headers["Content-Type"] = content_type
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            upstream = await client.request(
                request.method,
                target,
                params=params,
                content=await request.body(),
                headers=headers,
            )
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="高德 JS API 代理请求失败") from exc

    response_headers = {"Cache-Control": upstream.headers.get("cache-control", "no-store")}
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json").split(";", 1)[0],
        headers=response_headers,
    )
