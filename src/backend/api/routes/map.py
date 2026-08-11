"""Zoo map and AMap proxy endpoints."""

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from src.backend.api.dependencies import get_amap_client, get_repository
from src.backend.domain.models import MapGuideResponse
from src.backend.integrations.amap.client import (
    AmapServiceError,
    amap_proxy_target,
)

router = APIRouter(tags=["map"])


@router.get("/api/map", response_model=MapGuideResponse)
def get_map_guide() -> MapGuideResponse:
    """Return venue coordinates and safe map configuration."""

    try:
        return get_amap_client().build_guide(get_repository().site_summaries())
    except (AmapServiceError, httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="地图服务暂不可用，请稍后重试") from exc


@router.get("/api/map/image", response_class=Response)
def get_map_image() -> Response:
    """Proxy the static map so the Web Service key stays server-side."""

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


@router.get("/_AMapService/{path:path}", response_class=Response)
async def proxy_amap_js_get(path: str, request: Request) -> Response:
    """Proxy an allow-listed JS API GET request."""

    return await _proxy_amap_js_service(path, request)


@router.post("/_AMapService/{path:path}", response_class=Response)
async def proxy_amap_js_post(path: str, request: Request) -> Response:
    """Proxy an allow-listed JS API POST request."""

    return await _proxy_amap_js_service(path, request)


async def _proxy_amap_js_service(path: str, request: Request) -> Response:
    """Append the secret jscode and relay one AMap JS API request."""

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

    response_headers = {
        "Cache-Control": upstream.headers.get("cache-control", "no-store")
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json").split(";", 1)[0],
        headers=response_headers,
    )
