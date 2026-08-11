"""Realtime voice endpoint."""

from fastapi import APIRouter, WebSocket

from src.backend.api.dependencies import get_audio_realtime
from src.backend.integrations.audio.realtime import AudioRealtimeError

router = APIRouter(prefix="/api/guide", tags=["voice"])


@router.websocket("/voice")
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
