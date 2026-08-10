"""Agno-powered conversational guide with resumable user input."""

from __future__ import annotations

import json
import os
import re
from ast import literal_eval
from pathlib import Path
from typing import Any
from uuid import uuid4

from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.models.openai.like import OpenAILike
from agno.run.agent import RunOutput
from agno.tools.user_control_flow import UserControlFlowTools
from dotenv import load_dotenv
from pydantic import TypeAdapter, ValidationError

from .amap_client import AmapClient
from .guide_tools import ZooGuideTools, normalize_site_list
from .repository import AnimalRepository
from .schemas import (
    GuideChatResponse,
    GuideInputField,
    GuideMapContext,
    RouteOption,
)

RUNTIME_DIR = Path(__file__).resolve().parents[1] / "data" / "runtime"
_SESSION_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,100}")
_ROUTE_LIST = TypeAdapter(list[RouteOption])


class GuideAgentError(RuntimeError):
    """Raised for invalid configuration or an unusable agent result."""


class GuideAgentService:
    """Translate FastAPI chat requests to Agno runs and HITL continuations."""

    def __init__(self, amap: AmapClient, repository: AnimalRepository) -> None:
        load_dotenv()
        api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
        base_url = os.getenv("LLM_BASE_URL", "").strip()
        model_id = os.getenv("LLM_MODEL", "").strip()
        if not api_key or not base_url or not model_id:
            raise GuideAgentError("请配置 DASHSCOPE_API_KEY、LLM_BASE_URL 和 LLM_MODEL")

        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        self.amap = amap
        self.repository = repository
        self.tools = ZooGuideTools(amap, repository)
        self.agent = Agent(
            id="hongshan-route-guide",
            name="红山森林导览员",
            model=OpenAILike(
                id=model_id,
                api_key=api_key,
                base_url=base_url,
                extra_body={"enable_thinking": False},
                timeout=90,
                max_retries=2,
            ),
            db=SqliteDb(
                db_file=str(RUNTIME_DIR / "guide_agent.db"),
                session_table="guide_agent_sessions",
            ),
            tools=[
                self.tools.search_animals_and_venues,
                self.tools.plan_zoo_routes,
                UserControlFlowTools(
                    instructions=(
                        "只询问完成本次路线规划真正缺少的信息；相关字段尽量一次询问，"
                        "使用中文描述，绝不重复询问已有信息。"
                    )
                ),
            ],
            instructions=_INSTRUCTIONS,
            add_history_to_context=True,
            num_history_runs=5,
            max_tool_calls_from_history=4,
            markdown=False,
            reasoning=False,
            telemetry=False,
        )

    async def chat(
        self,
        message: str,
        session_id: str | None,
        map_context: GuideMapContext,
    ) -> GuideChatResponse:
        session = _session_id(session_id)
        context = {
            "visitor_message": message,
            "map_context": map_context.model_dump(mode="json"),
            "available_venues": [site.name for site in self.repository.site_summaries()],
        }
        output = await self.agent.arun(
            json.dumps(context, ensure_ascii=False),
            session_id=session,
        )
        return self._response(output, session)

    async def continue_run(
        self,
        run_id: str,
        session_id: str,
        values: dict[str, str | int | float | bool],
    ) -> GuideChatResponse:
        session = _session_id(session_id)
        output = await self.agent.aget_run_output(run_id=run_id, session_id=session)
        if output is None or output.session_id != session:
            raise GuideAgentError("找不到需要继续的导览会话")
        required = {
            field.name: field
            for requirement in output.active_requirements
            if requirement.needs_user_input
            for field in (requirement.user_input_schema or [])
            if field.value is None
        }
        missing = [name for name in required if name not in values]
        if missing:
            raise GuideAgentError(f"还缺少输入：{'、'.join(missing)}")
        coerced = {
            name: _coerce_value(values[name], field.field_type)
            for name, field in required.items()
        }
        for requirement in output.active_requirements:
            if requirement.needs_user_input:
                requirement.provide_user_input(coerced)
        continued = await self.agent.acontinue_run(
            run_id=output.run_id,
            requirements=output.requirements,
            session_id=session,
        )
        return self._response(continued, session)

    @staticmethod
    def _response(output: RunOutput, session_id: str) -> GuideChatResponse:
        fields: list[GuideInputField] = []
        if output.is_paused:
            for requirement in output.active_requirements:
                if not requirement.needs_user_input:
                    continue
                for field in requirement.user_input_schema or []:
                    if field.value is None:
                        fields.append(
                            GuideInputField(
                                name=field.name,
                                field_type=str(field.field_type),
                                description=field.description or field.name,
                            )
                        )
            return GuideChatResponse(
                session_id=session_id,
                run_id=output.run_id,
                status="input_required",
                assistant_message="为了把路线安排得更合适，还需要你补充一点信息。",
                required_inputs=fields,
            )

        return GuideChatResponse(
            session_id=session_id,
            run_id=output.run_id,
            status="completed",
            assistant_message=_content_text(output.content),
            route_options=_extract_routes(output),
        )


def _extract_routes(output: RunOutput) -> list[RouteOption]:
    for tool in reversed(output.tools or []):
        if tool.tool_name != "plan_zoo_routes" or tool.tool_call_error:
            continue
        payload = tool.result
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                try:
                    payload = literal_eval(payload)
                except (ValueError, SyntaxError):
                    continue
        if isinstance(payload, dict) and "routes" in payload:
            try:
                return _ROUTE_LIST.validate_python(payload["routes"])
            except ValidationError:
                continue
    return []


def _content_text(content: Any) -> str:
    if isinstance(content, str) and content.strip():
        return content.strip()
    return "路线已经准备好，选择一张方案卡就能在地图上查看。"


def _session_id(value: str | None) -> str:
    if value is None:
        return uuid4().hex
    if not _SESSION_PATTERN.fullmatch(value):
        raise GuideAgentError("session_id 格式无效")
    return value


def _coerce_value(value: str | int | float | bool, field_type: Any) -> Any:
    kind = str(field_type).lower()
    if "int" in kind:
        return int(value)
    if "float" in kind:
        return float(value)
    if "bool" in kind and isinstance(value, str):
        return value.casefold() in {"1", "true", "yes", "是"}
    return value


_site_list = normalize_site_list


_INSTRUCTIONS = """
你是南京红山森林动物园的友好导览员。你的任务是理解游客需求并调用工具生成真实路线。

规则：
1. 路线、距离、时间和卡路里只能来自 plan_zoo_routes，绝不自行编造。
2. 规划前必须知道 available_minutes 和 energy_level（轻松、一般、充沛）。缺少时调用 get_user_input。
3. 优先使用 map_context 中 selected_sites 和 origin；不要重复询问已有值。
4. 用户提到动物但没有场馆时，先调用 search_animals_and_venues。
5. 体重是可选项；除非用户要求精确卡路里，否则不要强制询问。
6. 用户没有选场馆或动物时，用 get_user_input 询问最想看的动物或场馆。
7. 调用 plan_zoo_routes 后，用简短中文概括三个方案和任何阶梯、超时提示。
8. 不讨论园外交通，不声称路线具备无障碍或坡度保证。
""".strip()
