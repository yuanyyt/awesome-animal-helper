"""Conversational guide endpoints."""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from src.backend.agents.guide import GuideAgentError
from src.backend.api.dependencies import get_guide_agent
from src.backend.domain.models import (
    GuideChatRequest,
    GuideChatResponse,
    GuideContinueRequest,
)
from src.backend.integrations.provider_errors import (
    api_balance_detail,
    is_api_balance_exhausted,
)

router = APIRouter(prefix="/api/guide", tags=["guide"])


def _stream_response(events: AsyncIterator[str | GuideChatResponse]) -> StreamingResponse:
    async def encoded_events() -> AsyncIterator[str]:
        try:
            async for event in events:
                payload = (
                    {"type": "delta", "content": event}
                    if isinstance(event, str)
                    else {"type": "response", "data": event.model_dump(mode="json")}
                )
                yield json.dumps(payload, ensure_ascii=False) + "\n"
        except GuideAgentError as exc:
            code = "API_BALANCE_EXHAUSTED" if is_api_balance_exhausted(exc) else None
            yield json.dumps(
                {"type": "error", "message": str(exc), "code": code},
                ensure_ascii=False,
            ) + "\n"
        except Exception as exc:
            code = "API_BALANCE_EXHAUSTED" if is_api_balance_exhausted(exc) else None
            message = "API 余额不足，请检查模型服务配置" if code else "导览员暂时无法回答，请稍后重试"
            yield json.dumps(
                {"type": "error", "message": message, "code": code},
                ensure_ascii=False,
            ) + "\n"

    return StreamingResponse(
        encoded_events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/chat", response_model=GuideChatResponse)
async def chat_with_guide(request: GuideChatRequest) -> GuideChatResponse:
    """Start one conversational route-planning turn."""

    try:
        return await get_guide_agent().chat(
            request.message,
            request.session_id,
            request.map_context,
            request.enabled_capabilities,
        )
    except GuideAgentError as exc:
        if is_api_balance_exhausted(exc):
            raise HTTPException(status_code=402, detail=api_balance_detail()) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        if is_api_balance_exhausted(exc):
            raise HTTPException(status_code=402, detail=api_balance_detail()) from exc
        raise HTTPException(status_code=503, detail="导览员暂时无法回答，请稍后重试") from exc


@router.post("/chat/stream")
async def stream_guide_chat(request: GuideChatRequest) -> StreamingResponse:
    """Stream one conversational turn as newline-delimited JSON."""

    return _stream_response(
        get_guide_agent().chat_stream(
            request.message,
            request.session_id,
            request.map_context,
            request.enabled_capabilities,
        )
    )


@router.post("/chat/{run_id}/continue", response_model=GuideChatResponse)
async def continue_guide_chat(
    run_id: str,
    request: GuideContinueRequest,
) -> GuideChatResponse:
    """Resolve current HITL fields and resume a paused run."""

    try:
        return await get_guide_agent().continue_run(
            run_id,
            request.session_id,
            request.values,
        )
    except GuideAgentError as exc:
        if is_api_balance_exhausted(exc):
            raise HTTPException(status_code=402, detail=api_balance_detail()) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        if is_api_balance_exhausted(exc):
            raise HTTPException(status_code=402, detail=api_balance_detail()) from exc
        raise HTTPException(status_code=503, detail="导览会话暂时无法继续，请稍后重试") from exc


@router.post("/chat/{run_id}/continue/stream")
async def stream_continued_guide_chat(
    run_id: str,
    request: GuideContinueRequest,
) -> StreamingResponse:
    """Stream a resumed HITL turn as newline-delimited JSON."""

    return _stream_response(
        get_guide_agent().continue_run_stream(
            run_id,
            request.session_id,
            request.values,
        )
    )
