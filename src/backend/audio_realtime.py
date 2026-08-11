"""Qwen-Audio realtime speech-to-text bridge for editable chat drafts."""

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
    """Transcribe browser PCM without automatically submitting the result."""

    def __init__(
        self,
        config: AudioRealtimeConfig | None = None,
        connector: Callable[..., Any] = connect,
    ) -> None:
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
        session: VoiceSession,
        config: AudioRealtimeConfig,
    ) -> None:
        self.browser = browser
        self.upstream = upstream
        self.session = session
        self.config = config
        self.awaiting_transcript = False
        self.awaiting_speech = False

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
                await self.browser.send_json({"type": "state", "state": "transcribing"})
            elif event_type == "cancel":
                self.awaiting_transcript = False
                await self._send_upstream({"type": "input_audio_buffer.clear"})
                await self.browser.send_json({"type": "state", "state": "idle"})
            elif event_type == "speak":
                await self._speak(str(event.get("text", "")))
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
            elif event_type == "conversation.item.input_audio_transcription.delta":
                text = str(event.get("text") or event.get("delta") or "")
                if self.awaiting_transcript and text:
                    await self.browser.send_json(
                        {"type": "transcript.user.delta", "text": text}
                    )
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
            elif event_type == "error":
                self.awaiting_transcript = False
                self.awaiting_speech = False
                error = event.get("error") or {}
                await self.browser.send_json(
                    {"type": "error", "message": error.get("message", "实时语音请求失败")}
                )
                await self.browser.send_json({"type": "state", "state": "idle"})
            elif event_type == "response.audio.delta" and self.awaiting_speech:
                if audio := str(event.get("delta", "")):
                    await self.browser.send_bytes(base64.b64decode(audio, validate=True))
            elif event_type == "response.done" and self.awaiting_speech:
                self.awaiting_speech = False
                await self.browser.send_json({"type": "speech.done"})
                await self.browser.send_json({"type": "state", "state": "idle"})

    async def _handle_transcript(self, transcript: str) -> None:
        text = transcript.strip()
        if not text:
            await self.browser.send_json({"type": "error", "message": "没有听清楚，请再说一次"})
            await self.browser.send_json({"type": "state", "state": "idle"})
            return
        await self.browser.send_json({"type": "transcript.user.done", "text": text})
        await self.browser.send_json({"type": "state", "state": "idle"})

    async def _speak(self, text: str) -> None:
        text = text.strip()
        if not text:
            raise ValueError("语音回复内容不能为空")
        if len(text) > 8000:
            raise ValueError("语音回复内容过长")
        if self.awaiting_speech:
            await self._send_upstream({"type": "response.cancel"})
        self.awaiting_speech = True
        await self._send_upstream(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"请自然、逐字朗读下面的导览回复，不要添加任何内容：\n{text}",
                        }
                    ],
                },
            }
        )
        await self._send_upstream(
            {"type": "response.create", "response": {"modalities": ["audio", "text"]}}
        )
        await self.browser.send_json({"type": "state", "state": "speaking"})

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
收到游客音频时，只准确转写中文，不主动回答、不调用工具、不补充或改写内容。
收到明确的文字朗读指令时，只自然朗读指定正文，不添加开场白、解释或结尾。
""".strip()
