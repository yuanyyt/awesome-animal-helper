"""Agno-powered conversational guide with resumable user input."""

from __future__ import annotations

import json
import os
import re
from ast import literal_eval
from pathlib import Path
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.models.openai.like import OpenAILike
from agno.run import RunContext
from agno.run.agent import RunContentEvent, RunOutput
from agno.tools.function import Function
from agno.tools.user_control_flow import UserControlFlowTools
from dotenv import load_dotenv
from pydantic import TypeAdapter, ValidationError

from src.backend.agents.tools import ZooGuideTools, normalize_site_list
from src.backend.domain.models import (
    GuideChatResponse,
    GuideCapability,
    GuideInputField,
    GuideMapContext,
    RouteOption,
)
from src.backend.integrations.amap.client import AmapClient
from src.backend.knowledge import KnowledgeService
from src.backend.repositories.animals import AnimalRepository
from src.backend.repositories.wiki import WikiRepository
from src.backend.services.guide_intent import GuideTurnResolver, TurnResolution

RUNTIME_DIR = Path(__file__).resolve().parents[2] / "data" / "runtime"
_SESSION_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,100}")
_ROUTE_LIST = TypeAdapter(list[RouteOption])
_ROUTE_FIELDS = {
    "available_minutes": ("计划游览时长（分钟）", "int"),
    "energy_level": ("体力状况（轻松、一般或充沛）", "str"),
    "transport_preference": ("游览方式（纯步行或可乘观光车）", "str"),
}
_FORCE_USER_INPUT = {"type": "function", "function": {"name": "get_user_input"}}
_PLAIN_INPUT_REQUEST = re.compile(
    r"请.{0,12}(?:提供|选择|补充|告诉)|需要.{0,12}(?:了解|知道|确认)|"
    r"还缺少|为了.{0,20}需要"
)


class GuideAgentError(RuntimeError):
    """Raised for invalid configuration or an unusable agent result."""


class GuideAgentService:
    """Translate FastAPI chat requests to Agno runs and HITL continuations."""

    def __init__(
        self,
        amap: AmapClient,
        repository: AnimalRepository,
        knowledge: KnowledgeService | None = None,
        wiki: WikiRepository | None = None,
    ) -> None:
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
        self.tools = ZooGuideTools(amap, repository, knowledge=knowledge, wiki=wiki)
        self.agent = self._build_agent(
            api_key,
            base_url,
            model_id,
            tools=self._tools_for_run,
        )
        self.hitl_agent = self._build_agent(
            api_key,
            base_url,
            model_id,
            tools=[self._user_input_tools()],
            tool_choice=_FORCE_USER_INPUT,
        )

    def _build_agent(
        self,
        api_key: str,
        base_url: str,
        model_id: str,
        *,
        tools: Any,
        tool_choice: dict[str, Any] | None = None,
    ) -> Agent:
        return Agent(
            id="hongshan-route-guide",
            name="红山森林导览员",
            model=OpenAILike(
                id=model_id,
                api_key=api_key,
                base_url=base_url,
                extra_body=_model_extra_body(),
                request_params={"parallel_tool_calls": True},
                timeout=90,
                max_retries=2,
            ),
            db=SqliteDb(
                db_file=str(RUNTIME_DIR / "guide_agent.db"),
                session_table="guide_agent_sessions",
            ),
            tools=tools,
            instructions=_INSTRUCTIONS,
            add_history_to_context=True,
            num_history_runs=3,
            max_tool_calls_from_history=0,
            markdown=False,
            reasoning=False,
            telemetry=False,
            tool_choice=tool_choice,
            cache_callables=False,
        )

    def _tools_for_run(self, run_context: RunContext) -> list[Any]:
        """Expose every guide tool; UI choices are preferences, not permissions."""

        route_tool = Function.from_callable(
            self.tools.plan_zoo_routes_for_agent,
            name="plan_zoo_routes",
        )
        # Accept stale or hallucinated calls without advertising this internal field.
        route_tool.parameters.get("properties", {}).pop(
            "must_see_site_groups", None
        )
        return [
            Function.from_callable(
                self.tools.get_current_zoo_time,
                name="get_current_zoo_time",
            ),
            self._user_input_tools(),
            route_tool,
            Function.from_callable(
                self.tools.plan_zoo_navigation_for_agent,
                name="plan_zoo_navigation",
            ),
            Function.from_callable(
                self.tools.search_animal_knowledge_for_agent,
                name="search_animal_knowledge",
            ),
            Function.from_callable(
                self.tools.get_neighboring_knowledge_chunks,
                name="get_neighboring_knowledge_chunks",
            ),
            Function.from_callable(
                self.tools.search_animal_wiki_stories,
                name="search_animal_wiki_stories",
            ),
            Function.from_callable(
                self.tools.search_zoo_facilities_for_agent,
                name="search_zoo_facilities",
            ),
            Function.from_callable(
                self.tools.get_zoo_education_schedule,
                name="get_zoo_education_schedule",
            ),
        ]

    @staticmethod
    def _user_input_tools() -> UserControlFlowTools:
        return UserControlFlowTools(
            instructions=(
                "只要完成当前任务所需的数据无法从本轮消息、地图上下文或工具结果中可靠获得，"
                "必须调用 get_user_input 暂停运行，不能在普通回复中直接提问，也不能猜测或填默认值。"
                "一次提交所有缺失字段，使用中文描述；已有信息不得重复询问。"
            )
        )

    async def chat(
        self,
        message: str,
        session_id: str | None,
        map_context: GuideMapContext,
        enabled_capabilities: list[GuideCapability],
    ) -> GuideChatResponse:
        session, turn, dependencies, context = self._start_turn(
            message, session_id, map_context, enabled_capabilities
        )
        output = await self.agent.arun(
            json.dumps(context, ensure_ascii=False),
            session_id=session,
            dependencies=dependencies,
            metadata={"guide_turn": dependencies},
        )
        if not output.is_paused and _requests_plain_input(output):
            output = await self._force_user_input(
                session,
                dependencies,
                rejected_message=_content_text(output.content),
            )
        return self._response(output, session, turn)

    async def chat_stream(
        self,
        message: str,
        session_id: str | None,
        map_context: GuideMapContext,
        enabled_capabilities: list[GuideCapability],
    ) -> AsyncIterator[str | GuideChatResponse]:
        """Stream assistant text, followed by the completed structured response."""

        session, turn, dependencies, context = self._start_turn(
            message, session_id, map_context, enabled_capabilities
        )
        events = self.agent.arun(
            json.dumps(context, ensure_ascii=False),
            stream=True,
            yield_run_output=True,
            session_id=session,
            dependencies=dependencies,
            metadata={"guide_turn": dependencies},
        )
        output: RunOutput | None = None
        async for event in events:
            if isinstance(event, RunOutput):
                output = event
            elif isinstance(event, RunContentEvent) and isinstance(event.content, str):
                yield event.content
        if output is None:
            raise GuideAgentError("导览员未返回完整结果，请重试")
        if not output.is_paused and _requests_plain_input(output):
            output = await self._force_user_input(
                session,
                dependencies,
                rejected_message=_content_text(output.content),
            )
        yield self._response(output, session, turn)

    def _start_turn(
        self,
        message: str,
        session_id: str | None,
        map_context: GuideMapContext,
        enabled_capabilities: list[GuideCapability],
    ) -> tuple[str, TurnResolution, dict[str, object], dict[str, object]]:
        session = _session_id(session_id)
        turn = self.resolver.resolve(message, map_context)
        dependencies = turn.as_dependencies(map_context)
        preferences = _tool_preferences(enabled_capabilities)
        dependencies["tool_preferences"] = preferences
        context: dict[str, object] = {
            "visitor_message": message,
            "animal_names": list(turn.animal_names),
            "resolved_sites": list(turn.resolved_sites),
            "must_see_sites": list(turn.must_see_sites),
            "unresolved_terms": list(turn.unresolved_terms),
            "map_context": map_context.model_dump(mode="json"),
            "tool_preferences": preferences,
            "available_venues": [site.name for site in self.repository.site_summaries()],
        }
        return session, turn, dependencies, context

    async def _force_user_input(
        self,
        session_id: str,
        dependencies: dict[str, object],
        *,
        rejected_message: str | None = None,
    ) -> RunOutput:
        task = {
            "task": "上一条回答错误地在正文索要信息。仅调用 get_user_input，把其中确实缺失的信息转成简洁的中文表单字段",
            "rejected_assistant_message": rejected_message or "",
        }
        output = await self.hitl_agent.arun(
            json.dumps(task, ensure_ascii=False),
            session_id=session_id,
            dependencies=dependencies,
            metadata={"guide_turn": dependencies},
        )
        actual_fields = _pending_input_names(output)
        if not output.is_paused or not actual_fields:
            raise GuideAgentError("导览员未能正确发起信息补充请求，请重试")
        return output

    async def continue_run(
        self,
        run_id: str,
        session_id: str,
        values: dict[str, str | int | float | bool],
    ) -> GuideChatResponse:
        session, output, dependencies, metadata = await self._prepare_continuation(
            run_id, session_id, values
        )
        continued = await self.agent.acontinue_run(
            run_id=output.run_id,
            requirements=output.requirements,
            session_id=session,
            dependencies=dependencies,
            metadata=metadata,
        )
        return self._response(continued, session)

    async def continue_run_stream(
        self,
        run_id: str,
        session_id: str,
        values: dict[str, str | int | float | bool],
    ) -> AsyncIterator[str | GuideChatResponse]:
        """Resume a paused run and stream its assistant text."""

        session, output, dependencies, metadata = await self._prepare_continuation(
            run_id, session_id, values
        )
        events = self.agent.acontinue_run(
            run_id=output.run_id,
            requirements=output.requirements,
            stream=True,
            yield_run_output=True,
            session_id=session,
            dependencies=dependencies,
            metadata=metadata,
        )
        continued: RunOutput | None = None
        async for event in events:
            if isinstance(event, RunOutput):
                continued = event
            elif isinstance(event, RunContentEvent) and isinstance(event.content, str):
                yield event.content
        if continued is None:
            raise GuideAgentError("导览会话未返回完整结果，请重试")
        yield self._response(continued, session)

    async def _prepare_continuation(
        self,
        run_id: str,
        session_id: str,
        values: dict[str, str | int | float | bool],
    ) -> tuple[str, RunOutput, dict[str, object], dict[str, Any]]:
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
        dependencies["used_tool_kinds"] = sorted(_tool_kinds(output, dependencies))
        metadata = dict(output.metadata or {})
        metadata["guide_turn"] = dependencies
        return session, output, dependencies, metadata

    def _response(
        self,
        output: RunOutput,
        session_id: str,
        turn: TurnResolution | None = None,
    ) -> GuideChatResponse:
        turn_data = _stored_turn(output)
        if not turn_data and turn:
            turn_data = turn.as_dependencies(GuideMapContext())
        tool_kinds = _tool_kinds(output, turn_data)
        intent = _intent_from_tool_kinds(tool_kinds, output)
        animal_names = [
            item for item in turn_data.get("animal_names", []) if isinstance(item, str)
        ]
        knowledge_items = []
        if "animal" in tool_kinds:
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
                                description=_input_description(
                                    field.name,
                                    field.description,
                                ),
                            )
                        )
            return GuideChatResponse(
                session_id=session_id,
                run_id=output.run_id,
                status="input_required",
                assistant_message=(
                    "为了把路线安排得更合适，还需要你补充一点信息。"
                    if intent in {"route", "mixed"}
                    else "继续之前，还需要你补充一点信息。"
                ),
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
        if (
            tool.tool_name not in {"plan_zoo_routes", "plan_zoo_navigation"}
            or tool.tool_call_error
        ):
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


def _pending_input_names(output: RunOutput) -> list[str]:
    return [
        field.name
        for requirement in output.active_requirements
        if requirement.needs_user_input
        for field in (requirement.user_input_schema or [])
        if field.value is None and field.name
    ]


def _input_description(name: str, description: str | None) -> str:
    if name in _ROUTE_FIELDS:
        return _ROUTE_FIELDS[name][0]
    return description or "请补充所需信息"


def _string_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _tool_preferences(value: object) -> list[GuideCapability]:
    allowed: tuple[GuideCapability, ...] = ("route", "animal", "service")
    if not isinstance(value, list):
        return ["route"]
    enabled = [item for item in allowed if item in value]
    return enabled or ["route"]


def _tool_kinds(output: RunOutput, stored: dict[str, Any]) -> set[str]:
    kinds = {
        item
        for item in stored.get("used_tool_kinds", [])
        if item in {"route", "animal", "facility"}
    }
    for tool in output.tools or []:
        if tool.tool_call_error:
            continue
        if tool.tool_name in {"plan_zoo_routes", "plan_zoo_navigation"}:
            kinds.add("route")
        elif tool.tool_name in {
            "search_animal_knowledge",
            "get_neighboring_knowledge_chunks",
            "search_animal_wiki_stories",
        }:
            kinds.add("animal")
        elif tool.tool_name in {"search_zoo_facilities", "get_zoo_education_schedule"}:
            kinds.add("facility")
    return kinds


def _intent_from_tool_kinds(kinds: set[str], output: RunOutput) -> str:
    route_requested = "route" in kinds or (
        output.is_paused
        and any(name in _ROUTE_FIELDS for name in _pending_input_names(output))
    )
    if route_requested and "animal" in kinds:
        return "mixed"
    if route_requested:
        return "route"
    if "animal" in kinds:
        return "animal_knowledge"
    if "facility" in kinds:
        return "facility"
    return "unknown"


def _unique_strings(*values: object) -> list[str]:
    return list(dict.fromkeys(item for value in values for item in _string_items(value)))


def _content_text(content: Any) -> str:
    if isinstance(content, str) and content.strip():
        return content.strip()
    return "路线已经准备好，选择一张方案卡就能在地图上查看。"


def _requests_plain_input(output: RunOutput) -> bool:
    if output.is_paused or output.tools:
        return False
    content = output.content if isinstance(output.content, str) else ""
    return bool(_PLAIN_INPUT_REQUEST.search(content))


def _session_id(value: str | None) -> str:
    if value is None:
        return uuid4().hex
    if not _SESSION_PATTERN.fullmatch(value):
        raise GuideAgentError("session_id 格式无效")
    return value


def _model_extra_body() -> dict[str, bool]:
    """Disable thinking by default while retaining an explicit environment override."""

    configured = os.getenv("LLM_ENABLE_THINKING", "").strip().casefold()
    return {"enable_thinking": configured in {"1", "true", "yes", "on"}}


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
你是南京红山森林动物园的友好导览员。所有业务工具始终可用，你要根据 visitor_message 的完整语义自行判断需要哪些工具。后端已完成动物到场馆的标准化，你必须遵守 resolved_sites。

规则：
0. tool_preferences 是游客在界面表达的路线、动物或服务偏好，只影响回答侧重点，不禁用任何工具。明确问题语义优先于偏好；不得要求游客先打开某个工具。
1. 路线、距离、时间和卡路里只能来自 plan_zoo_navigation 或 plan_zoo_routes，绝不自行编造。
2. 严格区分两类路线。用户已有明确目的地，说“怎么走”“带我去”“从A到B”“规划去某处”，包括明确的多个目的地时，调用 plan_zoo_navigation；它不需要游览时长、体力或出行方式，缺省 transport_preference=自动，会同时考虑步行和运营中的观光车，禁止为这些字段调用 get_user_input。只有用户要求一日游、完整游园、整体安排，或需要在多个候选场馆间按时间和体力取舍时，才调用 plan_zoo_routes；缺少游览时长、体力状况或游览方式时必须调用 get_user_input 一次性收集。
3. 路线 HITL 仅用于完整游园规划。点到点导航能从本轮消息、对话上下文或 map_context 确定起点和目的地时必须直接规划，不得在正文询问是否需要规划。真正无法解析起点或目的地时才澄清；不得猜测坐标，不得在正文中展示工具字段名和参数清单。
4. 完整游园规划会自动采用后端解析的候选场馆和必到场馆，并为每个已选动物选择一个最顺路的对应场馆；这些数据无需也不得作为工具参数传递。plan_zoo_navigation 按 destination_names 顺序导航，不添加顺路场馆。用户明确说“从某处出发”时把名称原样传给 origin_name；后续消息可沿用对话中已经明确的起点。没有明确起点时省略 origin_name，让工具使用地图起点。
5. 动物事实和园区故事只能依据动物知识工具。通用物种知识使用 search_animal_knowledge；公众号趣事、文章标题和谜面使用 search_animal_wiki_stories。查询具体成员或个体的身份、物种、性别、昵称、亲缘关系、饲养训练或园区经历时，必须在同一轮并行调用 search_animal_knowledge 和 search_animal_wiki_stories，综合两边资料后回答。
6. 一个问题同时包含知识查询和路线请求时，两部分都要完成。相互独立的动物知识、Wiki、设施与路线工具应在同一轮并行调用；只有后一个工具确实依赖前一个工具的结果时才串行调用。最终先回答动物资料，再说明路线。不得因工具偏好只完成其中一部分。
7. 问题像动物事实、园区趣事、文章标题或谜面时先尝试知识工具；只有真正无法判断用户目的时才调用 get_user_input 澄清，不要臆测。
8. 体重是可选项；除非用户要求精确卡路里，否则不要强制询问。
9. 两个路线工具都只返回一条路线。直接介绍工具结果，不得声称还有三个方案，也不要要求用户在多个方案中选择。
10. 不讨论园外交通，不声称路线具备无障碍或坡度保证。
11. 用户查询园区设施时调用 search_zoo_facilities；卫生间、家庭卫生间、母婴室、餐饮、咖啡、烘焙、商店、文创、市集、饮水、寄存、停车、游客中心、售票、警务、吸烟区和观光车站信息只能来自该工具。查询“文创”“购物”“市集”时使用 shopping 类别，查询“烘焙”时使用 restaurant 类别。用户同时问“怎么走”时，将明确起点传给 near_name；若用户没有点名具体设施，必须把 facilities 数组第一项的完整 name 传给 plan_zoo_navigation，不得自行改选更远、品类更丰富或所谓官方门店；用户点名设施时遵从其选择。不得只列设施后询问是否需要规划。仅问“有哪些”时只列设施。回答优先说明 nearby 邻近场馆，不讨论采集来源或精度。
12. 观光车为单向环线：北门站→猩猩馆站→中心广场站→东门站→猴山站→北门站。平日15元/人、8:30-16:00售票、8:30-16:30乘车；法定节假日20元/人、8:30-16:30售票、8:30-17:00乘车。身高1米以下儿童免票，车票当日有效、隔日作废，一经乘坐不予退换。
13. 回答观光车状态或使用 plan_zoo_routes 的可乘观光车规划前必须调用 get_current_zoo_time；plan_zoo_navigation 会自行查询运营状态并比较方案，无需额外调用。观光车车程按12km/h、每次上车候车5分钟估算；实际采用观光车时必须说明时间是估算，但不要把设施点位描述为估算。
14. 只有首轮知识片段指代不清、缺少前后文或用户明确要求完整故事时，才调用 get_neighboring_knowledge_chunks；只能传入本轮 search_animal_knowledge 返回的 chunk ID，不得猜测 ID。
15. CSV 结构化资料和 intro 讲解片段用于通用物种事实、场馆讲解，也可能包含个体名录；Wiki 用于具体个体、饲养训练、园区经历、公众号趣事、文章标题和谜面式问题。个体问题和无法判断资料类型时并行调用两个知识工具，不得因其中一个工具返回了同物种但不相关的资料而停止检索。
16. 用户要求“介绍一下”或同时询问物种知识与园区趣事时，在同一轮并行调用 search_animal_knowledge 和 search_animal_wiki_stories。
17. Wiki 事实只能表述为特定个体或特定时间的园区故事，不得泛化为整个物种的习性；引用时附上工具返回的文章标题和 URL。任一知识工具无结果时可使用另一个，但要说明资料类型。
18. 资料没有说明的内容要明确说不知道，不得补造。
19. 科普讲解和行为训练展示时间只能来自 get_zoo_education_schedule；区分工作日与节假日，并提醒游客因客流与场地限制，以现场实际工作为准。
20. 游客服务中心提供广播、问讯、滑板车寄存、邮政、投诉意见受理、医疗、手机充电、失物招领、免费饮水和休息座椅；免费租借雨伞、婴儿车和轮椅。联系电话：求助广播 025-8563 1157，团队业务咨询 025-8543 0087，园内紧急救援 025-8562 0039，野生动物救护 025-8579 9061，教育活动及动物认养咨询 025-8551 8101，其他咨询及投诉建议 025-8562 0178。
""".strip()
