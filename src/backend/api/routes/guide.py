"""Conversational guide endpoints."""

from fastapi import APIRouter, HTTPException

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
