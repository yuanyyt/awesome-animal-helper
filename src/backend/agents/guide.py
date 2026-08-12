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
from agno.run import RunContext
from agno.run.agent import RunOutput
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
            num_history_runs=5,
            max_tool_calls_from_history=4,
            markdown=False,
            reasoning=False,
            telemetry=False,
            tool_choice=tool_choice,
            cache_callables=False,
        )

    def _tools_for_run(self, run_context: RunContext) -> list[Any]:
        """Expose every guide tool; UI choices are preferences, not permissions."""

        return [
            Function.from_callable(
                self.tools.get_current_zoo_time,
                name="get_current_zoo_time",
            ),
            self._user_input_tools(),
            Function.from_callable(
                self.tools.plan_zoo_routes_for_agent,
                name="plan_zoo_routes",
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
        session = _session_id(session_id)
        turn = self.resolver.resolve(message, map_context)
        dependencies = turn.as_dependencies(map_context)
        preferences = _tool_preferences(enabled_capabilities)
        dependencies["tool_preferences"] = preferences
        context = {
            "visitor_message": message,
            "animal_names": list(turn.animal_names),
            "resolved_sites": list(turn.resolved_sites),
            "must_see_sites": list(turn.must_see_sites),
            "must_see_site_groups": [
                {"label": label, "sites": list(sites)}
                for label, sites in turn.must_see_site_groups
            ],
            "unresolved_terms": list(turn.unresolved_terms),
            "map_context": map_context.model_dump(mode="json"),
            "tool_preferences": preferences,
            "available_venues": [site.name for site in self.repository.site_summaries()],
        }
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
        continued = await self.agent.acontinue_run(
            run_id=output.run_id,
            requirements=output.requirements,
            session_id=session,
            dependencies=dependencies,
            metadata=metadata,
        )
        return self._response(continued, session)

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
        if tool.tool_name == "plan_zoo_routes":
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
    """Only override provider thinking mode when the environment requests it."""

    configured = os.getenv("LLM_ENABLE_THINKING", "").strip().casefold()
    if not configured:
        return {}
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
1. 路线、距离、时间和卡路里只能来自 plan_zoo_routes，绝不自行编造。
2. 只有用户确实要求路线时才调用 plan_zoo_routes。规划需要游览时长、体力状况和游览方式；从本轮消息或上下文能确定的值直接使用，缺少时必须调用 get_user_input 一次性收集。不得猜测、填默认值或在正文中提问，不得在正文中展示工具字段名和参数清单。
3. 当任务所需的其他关键数据无法从本轮消息、map_context、上下文或工具结果中可靠确定时，也必须调用 get_user_input 进入 HITL；不要只在正文中提问，不要自行补默认值。字段描述应具体说明用户要提供什么。
4. plan_zoo_routes 会把 resolved_sites 作为高优先级候选，把 must_see_sites 作为必到场馆，并为每个已选动物从 must_see_site_groups 中择一最顺路场馆；不要擅自提升、删除或重复动物场馆，也不要声称已解析目标未匹配，除非工具明确返回 unresolved_sites。用户明确说“从某处出发”时，把该名称原样传给 origin_name；不要为它编造坐标。没有明确命名起点时省略 origin_name，让工具使用地图起点。
5. 动物事实和园区故事只能依据动物知识工具。通用物种知识使用 search_animal_knowledge；园内有哪些某种动物、具体成员或个体、昵称、饲养训练、园区经历、公众号趣事、文章标题和谜面必须调用 search_animal_wiki_stories。
6. 一个问题同时包含知识查询和路线请求时，两部分都要完成：先调用匹配的动物知识工具，再在信息齐全后调用 plan_zoo_routes；最终先回答动物资料，再说明路线。不得因工具偏好只完成其中一部分。
7. 问题像动物事实、园区趣事、文章标题或谜面时先尝试知识工具；只有真正无法判断用户目的时才调用 get_user_input 澄清，不要臆测。
8. 体重是可选项；除非用户要求精确卡路里，否则不要强制询问。
9. plan_zoo_routes 只返回一条最符合用户明确时间、体力和出行方式的路线。直接介绍这条路线，不得声称还有三个方案，也不要要求用户在多个方案中选择。
10. 不讨论园外交通，不声称路线具备无障碍或坡度保证。
11. 用户查询园区设施时调用 search_zoo_facilities；卫生间、家庭卫生间、母婴室、餐饮、咖啡、烘焙、商店、文创、市集、饮水、寄存、停车、游客中心、售票、警务、吸烟区和观光车站信息只能来自该工具。查询“文创”“购物”“市集”时使用 shopping 类别，查询“烘焙”时使用 restaurant 类别；回答时优先说明工具返回的 nearby 邻近场馆。工具提供的所有点位均可正常使用，不讨论其采集来源或精度。
12. 观光车为单向环线：北门站→猩猩馆站→中心广场站→东门站→猴山站→北门站。平日15元/人、8:30-16:00售票、8:30-16:30乘车；法定节假日20元/人、8:30-16:30售票、8:30-17:00乘车。身高1米以下儿童免票，车票当日有效、隔日作废，一经乘坐不予退换。
13. 回答观光车状态或使用可乘观光车规划前必须调用 get_current_zoo_time。观光车车程按12km/h、每次上车候车5分钟估算；必须说明时间是估算，但不要把设施点位描述为估算。
14. 只有首轮知识片段指代不清、缺少前后文或用户明确要求完整故事时，才调用 get_neighboring_knowledge_chunks；只能传入本轮 search_animal_knowledge 返回的 chunk ID，不得猜测 ID。
15. CSV 结构化资料和 intro 讲解片段用于通用物种事实与场馆讲解；昵称、具体个体、饲养训练、园区经历、公众号趣事、文章标题和谜面式问题必须调用 search_animal_wiki_stories。无法判断资料类型时同时调用两个知识工具。
16. 用户要求“介绍一下”或同时询问物种知识与园区趣事时，先调用 search_animal_knowledge，再调用 search_animal_wiki_stories。
17. Wiki 事实只能表述为特定个体或特定时间的园区故事，不得泛化为整个物种的习性；引用时附上工具返回的文章标题和 URL。任一知识工具无结果时可使用另一个，但要说明资料类型。
18. 资料没有说明的内容要明确说不知道，不得补造。
19. 科普讲解和行为训练展示时间只能来自 get_zoo_education_schedule；区分工作日与节假日，并提醒游客因客流与场地限制，以现场实际工作为准。
20. 游客服务中心提供广播、问讯、滑板车寄存、邮政、投诉意见受理、医疗、手机充电、失物招领、免费饮水和休息座椅；免费租借雨伞、婴儿车和轮椅。联系电话：求助广播 025-8563 1157，团队业务咨询 025-8543 0087，园内紧急救援 025-8562 0039，野生动物救护 025-8579 9061，教育活动及动物认养咨询 025-8551 8101，其他咨询及投诉建议 025-8562 0178。
""".strip()
