"""Secure WebSocket bridge for Qwen-Audio realtime zoo conversations."""

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

from .guide_tools import ZooGuideTools
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


UpstreamConnector = Callable[..., Awaitable[ClientConnection]]
SyncRunner = Callable[..., Awaitable[Any]]


class AudioRealtimeService:
    """Relay browser PCM and execute local tools requested by Qwen-Audio."""

    def __init__(
        self,
        tools: ZooGuideTools,
        config: AudioRealtimeConfig | None = None,
        connector: Callable[..., Any] = connect,
    ) -> None:
        self.tools = tools
        self.config = config or AudioRealtimeConfig.from_env()
        self.connector = connector

    async def serve(self, browser: WebSocket) -> None:
        await browser.accept()
        try:
            context = await _receive_initial_context(browser)
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
                bridge = _AudioBridge(browser, upstream, self.tools, context, self.config)
                await bridge.run()
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
        tools: ZooGuideTools,
        context: GuideMapContext,
        config: AudioRealtimeConfig,
        run_sync: SyncRunner = asyncio.to_thread,
    ) -> None:
        self.browser = browser
        self.upstream = upstream
        self.tools = tools
        self.context = context
        self.config = config
        self.run_sync = run_sync
        self.needs_tool_followup = False

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
                    "max_history_turns": 20,
                    "turn_detection": None,
                    "tools": _TOOL_SCHEMAS,
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
            exception = task.exception()
            if exception is not None:
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
                self.context = GuideMapContext.model_validate(event.get("map_context", {}))
            elif event_type == "commit":
                await self._send_upstream({"type": "input_audio_buffer.commit"})
                await self._send_upstream({"type": "response.create"})
                await self.browser.send_json({"type": "state", "state": "thinking"})
            elif event_type == "cancel":
                await self._send_upstream({"type": "response.cancel"})
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
            elif event_type == "response.created":
                await self.browser.send_json({"type": "state", "state": "thinking"})
            elif event_type == "response.audio.delta":
                await self.browser.send_bytes(base64.b64decode(event.get("delta", "")))
                await self.browser.send_json({"type": "state", "state": "speaking"})
            elif event_type == "conversation.item.input_audio_transcription.completed":
                await self.browser.send_json(
                    {"type": "transcript.user.done", "text": event.get("transcript", "")}
                )
            elif event_type == "response.audio_transcript.delta":
                await self.browser.send_json(
                    {"type": "transcript.assistant.delta", "text": event.get("delta", "")}
                )
            elif event_type == "response.audio_transcript.done":
                await self.browser.send_json(
                    {"type": "transcript.assistant.done", "text": event.get("transcript", "")}
                )
            elif event_type == "response.function_call_arguments.done":
                await self._execute_tool(event)
            elif event_type == "response.done":
                if self.needs_tool_followup:
                    self.needs_tool_followup = False
                    await self._send_upstream(
                        {"type": "response.create", "response": {"modalities": ["audio", "text"]}}
                    )
                else:
                    await self.browser.send_json({"type": "state", "state": "idle"})
            elif event_type == "error":
                error = event.get("error") or {}
                await self.browser.send_json(
                    {"type": "error", "message": error.get("message", "实时语音请求失败")}
                )

    async def _execute_tool(self, event: dict[str, Any]) -> None:
        name = str(event.get("name", ""))
        call_id = str(event.get("call_id", ""))
        try:
            arguments = json.loads(event.get("arguments") or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("工具参数必须是对象")
            if name == "search_animals_and_venues":
                result = self.tools.search_animals_and_venues(str(arguments.get("query", "")))
            elif name == "plan_zoo_routes":
                result = await self.run_sync(self.tools.plan_with_context, arguments, self.context)
                await self.browser.send_json(
                    {"type": "route.options", "routes": result.get("routes", [])}
                )
            else:
                raise ValueError(f"不支持的工具：{name}")
        except Exception as exc:
            result = {"error": str(exc)}
            await self.browser.send_json({"type": "tool.error", "message": str(exc)})
        await self._send_upstream(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(result, ensure_ascii=False),
                },
            }
        )
        self.needs_tool_followup = True

    async def _send_upstream(self, event: dict[str, Any]) -> None:
        await self.upstream.send(json.dumps(event, ensure_ascii=False))


async def _receive_initial_context(browser: WebSocket) -> GuideMapContext:
    try:
        raw = await asyncio.wait_for(browser.receive_text(), timeout=CONFIG_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        raise AudioRealtimeError("语音会话配置超时") from exc
    event = json.loads(raw)
    if event.get("type") != "configure":
        raise AudioRealtimeError("首个语音事件必须是 configure")
    return GuideMapContext.model_validate(event.get("map_context", {}))


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
你是南京红山森林动物园里亲切、活泼的森林导览员，用简短自然的中文口语交流。
动物事实必须先调用 search_animals_and_venues，以本地资料为准；查不到时明确说明。
路线、距离、时间和卡路里必须调用 plan_zoo_routes，绝不自行编造。
规划路线前需要知道可用分钟数和体力状态（轻松、一般、充沛）；缺少时自然追问。
用户未口述场馆时，路线工具会自动采用地图上已选场馆，不要重复询问。
体重仅在用户要求精确卡路里时询问。工具返回三条路线后，简短比较并请用户在卡片中选择。
不要输出 Markdown、emoji 或冗长铺垫，不讨论园外交通，不声称路线具备无障碍保证。
""".strip()


_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_animals_and_venues",
            "description": "查询本地动物资料、趣味事实以及动物所在场馆。回答动物问题前必须调用。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "动物中文名或学名"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_zoo_routes",
            "description": "根据时间、体力、地图场馆和起点生成三条真实园内步行路线。",
            "parameters": {
                "type": "object",
                "properties": {
                    "available_minutes": {"type": "integer", "description": "可游览分钟数，至少30"},
                    "energy_level": {
                        "type": "string",
                        "enum": ["轻松", "一般", "充沛"],
                        "description": "游客体力状态",
                    },
                    "selected_sites": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "用户口述的候选场馆；未提供时使用地图选择",
                    },
                    "must_see_sites": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "必须游览的场馆",
                    },
                    "weight_kg": {"type": "number", "description": "可选体重，用于估算卡路里"},
                },
                "required": ["available_minutes", "energy_level"],
            },
        },
    },
]
