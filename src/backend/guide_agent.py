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
from agno.tools.function import Function
from agno.tools.user_control_flow import UserControlFlowTools
from dotenv import load_dotenv
from pydantic import TypeAdapter, ValidationError

from .amap_client import AmapClient
from .guide_intent import GuideTurnResolver, TurnResolution
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
        self.resolver = GuideTurnResolver(repository)
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
                Function.from_callable(
                    self.tools.search_animals_for_agent,
                    name="search_animals_and_venues",
                ),
                Function.from_callable(
                    self.tools.plan_zoo_routes_for_agent,
                    name="plan_zoo_routes",
                ),
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
        turn = self.resolver.resolve(message, map_context)
        dependencies = turn.as_dependencies(map_context)
        context = {
            "visitor_message": message,
            "intent": turn.intent,
            "animal_names": list(turn.animal_names),
            "resolved_sites": list(turn.resolved_sites),
            "must_see_sites": list(turn.must_see_sites),
            "unresolved_terms": list(turn.unresolved_terms),
            "map_context": map_context.model_dump(mode="json"),
            "available_venues": [site.name for site in self.repository.site_summaries()],
        }
        output = await self.agent.arun(
            json.dumps(context, ensure_ascii=False),
            session_id=session,
            dependencies=dependencies,
            metadata={"guide_turn": dependencies},
        )
        return self._response(output, session, turn)

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
        dependencies = _stored_turn(output)
        continued = await self.agent.acontinue_run(
            run_id=output.run_id,
            requirements=output.requirements,
            session_id=session,
            dependencies=dependencies,
            metadata=output.metadata,
        )
        return self._response(continued, session)

    def _response(
        self,
        output: RunOutput,
        session_id: str,
        turn: TurnResolution | None = None,
    ) -> GuideChatResponse:
        turn_data = turn.as_dependencies(GuideMapContext()) if turn else _stored_turn(output)
        intent = str(turn_data.get("intent", "unknown"))
        animal_names = [
            item for item in turn_data.get("animal_names", []) if isinstance(item, str)
        ]
        knowledge_items = []
        if intent in {"animal_knowledge", "mixed"}:
            knowledge_items = [
                animal
                for name in animal_names[:8]
                for animal in self.repository.query(name=name).items
            ]
        route_payload = _extract_route_payload(output)
        unresolved = _unique_strings(
            turn_data.get("unresolved_terms", []),
            route_payload.get("unresolved_sites", []),
        )
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
                intent=intent,
                resolved_sites=_string_items(turn_data.get("resolved_sites")),
                unresolved_terms=unresolved,
                knowledge_items=knowledge_items,
                required_inputs=fields,
            )

        return GuideChatResponse(
            session_id=session_id,
            run_id=output.run_id,
            status="completed",
            assistant_message=_content_text(output.content),
            intent=intent,
            resolved_sites=_string_items(
                route_payload.get("resolved_sites") or turn_data.get("resolved_sites")
            ),
            unresolved_terms=unresolved,
            knowledge_items=knowledge_items,
            route_options=_validate_routes(route_payload.get("routes")),
        )


def _extract_route_payload(output: RunOutput) -> dict[str, Any]:
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
            return payload
    return {}


def _validate_routes(value: Any) -> list[RouteOption]:
    try:
        return _ROUTE_LIST.validate_python(value or [])
    except ValidationError:
        return []


def _stored_turn(output: RunOutput) -> dict[str, Any]:
    metadata = output.metadata or {}
    turn = metadata.get("guide_turn")
    return turn if isinstance(turn, dict) else {}


def _string_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _unique_strings(*values: object) -> list[str]:
    return list(dict.fromkeys(item for value in values for item in _string_items(value)))


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
你是南京红山森林动物园的友好导览员。后端已完成意图识别和动物到场馆的标准化，你必须遵守输入中的 intent 与 resolved_sites。

规则：
1. 路线、距离、时间和卡路里只能来自 plan_zoo_routes，绝不自行编造。
2. intent 为 route 或 mixed 时才规划路线；规划前必须知道 available_minutes 和 energy_level（轻松、一般、充沛），缺少时调用 get_user_input。
3. plan_zoo_routes 会把 resolved_sites 作为高优先级候选，只把 must_see_sites 作为必到场馆；不要擅自提升或删除场馆，也不要声称已解析场馆未匹配，除非工具明确返回 unresolved_sites。
4. intent 为 animal_knowledge 或 mixed 时调用 search_animals_and_venues，只依据工具返回的本地资料回答。
5. intent 为 mixed 时先概括路线，再附一段简短动物介绍。
6. intent 为 unknown 时询问游客想规划路线还是了解动物，不调用路线工具。
7. 体重是可选项；除非用户要求精确卡路里，否则不要强制询问。
8. 工具返回路线后简短说明各方案在步行量和覆盖度上的差异，不重复输出大段免责声明。
9. 不讨论园外交通，不声称路线具备无障碍或坡度保证。
""".strip()
