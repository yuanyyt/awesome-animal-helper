"""Qwen-Audio realtime bridge using Agno as the sole conversation brain."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from websockets.asyncio.client import ClientConnection, connect

from .guide_agent import GuideAgentService
from .schemas import GuideMapContext

MAX_AUDIO_FRAME_BYTES = 64 * 1024
CONFIG_TIMEOUT_SECONDS = 15


class AudioRealtimeError(RuntimeError):
    """Raised when a realtime audio session cannot be configured."""


@dataclass(frozen=True)
class AudioRealtimeConfig:
    """Environment-backed Qwen realtime connection settings."""

    api_key: str
    base_url: str
    model: str = "qwen-audio-3.0-realtime-flash"
    voice: str = "longanqian"

    @classmethod
    def from_env(cls) -> "AudioRealtimeConfig":
        load_dotenv()
        api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
        source_url = (
            os.getenv("AUDIO_REALTIME_BASE_URL", "").strip()
            or os.getenv("LLM_BASE_URL", "").strip()
        )
        if not api_key or not source_url:
            raise AudioRealtimeError(
                "请配置 DASHSCOPE_API_KEY，并提供 AUDIO_REALTIME_BASE_URL 或 LLM_BASE_URL"
            )
        return cls(
            api_key=api_key,
            base_url=_realtime_base_url(source_url),
            model=os.getenv("AUDIO_REALTIME_MODEL", "").strip()
            or "qwen-audio-3.0-realtime-flash",
            voice=os.getenv("AUDIO_REALTIME_VOICE", "").strip() or "longanqian",
        )

    @property
    def url(self) -> str:
        return f"{self.base_url}?{urlencode({'model': self.model})}"


@dataclass
class VoiceSession:
    """Browser-owned context shared with the Agno text session."""

    map_context: GuideMapContext
    session_id: str | None = None


UpstreamConnector = Callable[..., Awaitable[ClientConnection]]


class AudioRealtimeService:
    """Transcribe browser PCM, call Agno, and speak Agno's final answer."""

    def __init__(
        self,
        guide: GuideAgentService,
        config: AudioRealtimeConfig | None = None,
        connector: Callable[..., Any] = connect,
    ) -> None:
        self.guide = guide
        self.config = config or AudioRealtimeConfig.from_env()
        self.connector = connector

    async def serve(self, browser: WebSocket) -> None:
        await browser.accept()
        try:
            session = await _receive_initial_context(browser)
            await browser.send_json({"type": "state", "state": "connecting"})
            async with self.connector(
                self.config.url,
                additional_headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "User-Agent": "hongshan-animal-guide/0.1",
                },
                max_size=8 * 1024 * 1024,
                close_timeout=2,
            ) as upstream:
                await _AudioBridge(
                    browser,
                    upstream,
                    self.guide,
                    session,
                    self.config,
                ).run()
        except WebSocketDisconnect:
            return
        except (AudioRealtimeError, ValidationError, ValueError) as exc:
            await _safe_send_json(browser, {"type": "error", "message": str(exc)})
            await _safe_close(browser, 1008)
        except Exception:
            await _safe_send_json(
                browser,
                {"type": "error", "message": "实时语音服务暂不可用，请稍后重试"},
            )
            await _safe_close(browser, 1011)


class _AudioBridge:
    def __init__(
        self,
        browser: WebSocket,
        upstream: ClientConnection,
        guide: GuideAgentService,
        session: VoiceSession,
        config: AudioRealtimeConfig,
    ) -> None:
        self.browser = browser
        self.upstream = upstream
        self.guide = guide
        self.session = session
        self.config = config
        self.awaiting_transcript = False
        self.reading_response = False

    async def run(self) -> None:
        await self._send_upstream(
            {
                "type": "session.update",
                "session": {
                    "modalities": ["audio", "text"],
                    "voice": self.config.voice,
                    "instructions": _VOICE_INSTRUCTIONS,
                    "input_audio_format": "pcm",
                    "output_audio_format": "pcm",
                    "max_history_turns": 6,
                    "turn_detection": None,
                },
            }
        )
        browser_task = asyncio.create_task(self._read_browser())
        upstream_task = asyncio.create_task(self._read_upstream())
        done, pending = await asyncio.wait(
            {browser_task, upstream_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            if exception := task.exception():
                raise exception

    async def _read_browser(self) -> None:
        while True:
            message = await self.browser.receive()
            if message["type"] == "websocket.disconnect":
                return
            if audio := message.get("bytes"):
                if len(audio) > MAX_AUDIO_FRAME_BYTES or len(audio) % 2:
                    raise ValueError("音频帧格式无效")
                await self._send_upstream(
                    {
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(audio).decode("ascii"),
                    }
                )
                continue
            raw = message.get("text")
            if raw is None:
                continue
            event = json.loads(raw)
            event_type = event.get("type")
            if event_type == "context.update":
                self.session.map_context = GuideMapContext.model_validate(
                    event.get("map_context", {})
                )
            elif event_type == "session.update":
                self.session.session_id = _optional_text(event.get("session_id"))
            elif event_type == "commit":
                self.awaiting_transcript = True
                await self._send_upstream({"type": "input_audio_buffer.commit"})
                await self.browser.send_json({"type": "state", "state": "thinking"})
            elif event_type == "cancel":
                if self.reading_response:
                    await self._send_upstream({"type": "response.cancel"})
                self.reading_response = False
                await self.browser.send_json({"type": "state", "state": "idle"})
            else:
                raise ValueError("未知的语音控制事件")

    async def _read_upstream(self) -> None:
        async for raw in self.upstream:
            if not isinstance(raw, str):
                continue
            event = json.loads(raw)
            event_type = event.get("type", "")
            if event_type == "session.updated":
                await self.browser.send_json({"type": "ready"})
                await self.browser.send_json({"type": "state", "state": "idle"})
            elif event_type == "conversation.item.input_audio_transcription.completed":
                if self.awaiting_transcript:
                    self.awaiting_transcript = False
                    await self._handle_transcript(str(event.get("transcript", "")))
            elif event_type == "conversation.item.input_audio_transcription.failed":
                self.awaiting_transcript = False
                await self.browser.send_json(
                    {"type": "error", "message": "没有听清楚，请再说一次"}
                )
                await self.browser.send_json({"type": "state", "state": "idle"})
            elif event_type == "response.created":
                self.reading_response = True
                await self.browser.send_json({"type": "state", "state": "speaking"})
            elif event_type == "response.audio.delta":
                await self.browser.send_bytes(base64.b64decode(event.get("delta", "")))
            elif event_type == "response.done":
                self.reading_response = False
                await self.browser.send_json({"type": "state", "state": "idle"})
            elif event_type == "error":
                error = event.get("error") or {}
                await self.browser.send_json(
                    {"type": "error", "message": error.get("message", "实时语音请求失败")}
                )

    async def _handle_transcript(self, transcript: str) -> None:
        text = transcript.strip()
        if not text:
            await self.browser.send_json({"type": "error", "message": "没有听清楚，请再说一次"})
            await self.browser.send_json({"type": "state", "state": "idle"})
            return
        await self.browser.send_json({"type": "transcript.user.done", "text": text})
        try:
            response = await self.guide.chat(
                text,
                self.session.session_id,
                self.session.map_context,
            )
        except Exception:
            await self.browser.send_json(
                {"type": "error", "message": "导览员暂时无法回答，请稍后重试"}
            )
            await self.browser.send_json({"type": "state", "state": "idle"})
            return
        self.session.session_id = response.session_id
        await self.browser.send_json(
            {"type": "guide.response", "response": response.model_dump(mode="json")}
        )
        await self._speak(response.assistant_message)

    async def _speak(self, text: str) -> None:
        await self._send_upstream(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"朗读稿如下，只朗读正文：\n{text}",
                        }
                    ],
                },
            }
        )
        await self._send_upstream(
            {"type": "response.create", "response": {"modalities": ["audio", "text"]}}
        )

    async def _send_upstream(self, event: dict[str, Any]) -> None:
        await self.upstream.send(json.dumps(event, ensure_ascii=False))


async def _receive_initial_context(browser: WebSocket) -> VoiceSession:
    try:
        raw = await asyncio.wait_for(browser.receive_text(), timeout=CONFIG_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        raise AudioRealtimeError("语音会话配置超时") from exc
    event = json.loads(raw)
    if event.get("type") != "configure":
        raise AudioRealtimeError("首个语音事件必须是 configure")
    return VoiceSession(
        map_context=GuideMapContext.model_validate(event.get("map_context", {})),
        session_id=_optional_text(event.get("session_id")),
    )


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _realtime_base_url(source_url: str) -> str:
    parsed = urlsplit(source_url)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.netloc:
        raise AudioRealtimeError("实时语音服务 URL 无效")
    if parsed.username or parsed.password:
        raise AudioRealtimeError("实时语音服务 URL 不应包含凭据")
    return urlunsplit(("wss", parsed.netloc, "/api-ws/v1/realtime", "", ""))


async def _safe_send_json(browser: WebSocket, payload: dict[str, Any]) -> None:
    try:
        await browser.send_json(payload)
    except RuntimeError:
        pass


async def _safe_close(browser: WebSocket, code: int) -> None:
    try:
        await browser.close(code=code)
    except RuntimeError:
        pass


_VOICE_INSTRUCTIONS = """
你只负责朗读应用传入的中文朗读稿，不回答游客问题，不调用工具，不补充或改写事实。
收到“朗读稿如下，只朗读正文”后，只自然朗读其后的正文；不要朗读“朗读稿”或其他指令文字。
语气亲切、清晰，适合亲子家庭在动物园内收听。
""".strip()
