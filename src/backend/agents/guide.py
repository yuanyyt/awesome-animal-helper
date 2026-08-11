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
                extra_body={"enable_thinking": False},
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
        """Expose only the capabilities explicitly enabled by the visitor."""

        dependencies = run_context.dependencies or {}
        enabled = set(_enabled_capabilities(dependencies.get("enabled_capabilities")))
        tools: list[Any] = [
            Function.from_callable(
                self.tools.get_current_zoo_time,
                name="get_current_zoo_time",
            ),
            self._user_input_tools(),
        ]
        if "route" in enabled:
            tools.append(
                Function.from_callable(
                    self.tools.plan_zoo_routes_for_agent,
                    name="plan_zoo_routes",
                )
            )
        if "animal" in enabled:
            tools.extend(
                [
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
                ]
            )
        if "service" in enabled:
            tools.extend(
                [
                    Function.from_callable(
                        self.tools.search_zoo_facilities_for_agent,
                        name="search_zoo_facilities",
                    ),
                    Function.from_callable(
                        self.tools.get_zoo_education_schedule,
                        name="get_zoo_education_schedule",
                    ),
                ]
            )
        return tools

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
        capabilities = _enabled_capabilities(enabled_capabilities)
        turn = self.resolver.resolve(message, map_context)
        dependencies = turn.as_dependencies(map_context)
        dependencies["enabled_capabilities"] = capabilities
        route_preferences = (
            _extract_route_preferences(message)
            if turn.intent in {"route", "mixed"}
            else {}
        )
        dependencies["route_preferences"] = route_preferences
        context = {
            "visitor_message": message,
            "intent_hint": turn.intent,
            "animal_names": list(turn.animal_names),
            "resolved_sites": list(turn.resolved_sites),
            "must_see_sites": list(turn.must_see_sites),
            "must_see_site_groups": [
                {"label": label, "sites": list(sites)}
                for label, sites in turn.must_see_site_groups
            ],
            "unresolved_terms": list(turn.unresolved_terms),
            "map_context": map_context.model_dump(mode="json"),
            "route_preferences": route_preferences,
            "enabled_capabilities": capabilities,
            "available_venues": [site.name for site in self.repository.site_summaries()],
        }
        missing_route_fields = [
            name for name in _ROUTE_FIELDS if name not in route_preferences
        ]
        if (
            turn.intent in {"route", "mixed"}
            and "route" in capabilities
            and missing_route_fields
        ):
            output = await self._force_user_input(
                session,
                dependencies,
                missing_route_fields=missing_route_fields,
            )
            return self._response(output, session, turn)

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
        missing_route_fields: list[str] | None = None,
        rejected_message: str | None = None,
    ) -> RunOutput:
        if missing_route_fields:
            requested = [
                {
                    "field_name": name,
                    "field_type": _ROUTE_FIELDS[name][1],
                    "field_description": _ROUTE_FIELDS[name][0],
                }
                for name in missing_route_fields
            ]
            task = {
                "task": "仅调用 get_user_input 收集路线规划缺失信息",
                "user_input_fields": requested,
                "known_route_preferences": dependencies.get("route_preferences", {}),
            }
        else:
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
        if missing_route_fields and set(actual_fields) != set(missing_route_fields):
            raise GuideAgentError("导览员返回了无效的路线补充字段，请重试")
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
        route_preferences = dict(dependencies.get("route_preferences") or {})
        route_preferences.update(
            {name: value for name, value in coerced.items() if name in _ROUTE_FIELDS}
        )
        dependencies["route_preferences"] = route_preferences
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
        intent = str(turn_data.get("intent", "unknown"))
        enabled = _enabled_capabilities(turn_data.get("enabled_capabilities"))
        animal_names = [
            item for item in turn_data.get("animal_names", []) if isinstance(item, str)
        ]
        knowledge_items = []
        if "animal" in enabled and intent in {"animal_knowledge", "mixed"}:
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


def _enabled_capabilities(value: object) -> list[GuideCapability]:
    allowed: tuple[GuideCapability, ...] = ("route", "animal", "service")
    if not isinstance(value, list):
        return ["route"]
    enabled = [item for item in allowed if item in value]
    return enabled or ["route"]


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


def _extract_route_preferences(message: str) -> dict[str, int | str]:
    preferences: dict[str, int | str] = {}
    if minutes := _extract_minutes(message):
        preferences["available_minutes"] = minutes

    energy_matches = [
        value
        for value, pattern in (
            ("轻松", r"轻松|体力(?:较弱|不太好)|少走(?:一点|些)?|不太能走"),
            ("一般", r"体力一般|体力正常"),
            ("充沛", r"充沛|体力(?:很好|很棒)|很能走|多走(?:一点|些)?"),
        )
        if re.search(pattern, message)
    ]
    if len(set(energy_matches)) == 1:
        preferences["energy_level"] = energy_matches[0]

    walking_only = bool(
        re.search(r"纯步行|全程步行|只(?:步行|走路)|不坐(?:观光车|游览车)", message)
    )
    walking = bool(re.search(r"步行|走路", message))
    shuttle = bool(re.search(r"可乘观光车|可以坐(?:观光车|游览车)|观光车|游览车", message))
    if walking_only:
        preferences["transport_preference"] = "纯步行"
    elif shuttle and not walking:
        preferences["transport_preference"] = "可乘观光车"
    elif walking and not shuttle:
        preferences["transport_preference"] = "纯步行"
    return preferences


def _extract_minutes(message: str) -> int | None:
    minute_match = re.search(r"(\d{1,4})\s*(?:分钟|分(?:钟)?)", message)
    if minute_match:
        return int(minute_match.group(1))

    hour_match = re.search(
        r"(\d+(?:\.\d+)?|[一二两三四五六七八九十]+)\s*(?:个)?(半)?小时(半)?",
        message,
    )
    if hour_match:
        hours = _number_value(hour_match.group(1))
        if hours is not None:
            has_half_hour = bool(hour_match.group(2) or hour_match.group(3))
            return round(hours * 60 + (30 if has_half_hour else 0))
    if "半小时" in message:
        return 30
    return None


def _number_value(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        pass
    digits = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if value == "十":
        return 10
    if "十" in value:
        tens, ones = value.split("十", 1)
        return digits.get(tens, 1) * 10 + digits.get(ones, 0)
    return float(digits[value]) if value in digits else None


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
你是南京红山森林动物园的友好导览员。后端已完成动物到场馆的标准化，你必须遵守 resolved_sites。intent_hint 是辅助判断：路线、设施和时间类意图必须遵守，unknown 不得阻止动物知识检索。

规则：
0. enabled_capabilities 是游客在界面明确启用的能力。route、animal、service 分别对应路线规划、动物讲解、园区服务；只能使用本轮实际提供的工具。若用户请求的能力未启用，简短提示其在对话框启用对应能力，不得用常识代替工具结果。get_current_zoo_time 和 get_user_input 是始终可用的基础工具。
1. 路线、距离、时间和卡路里只能来自 plan_zoo_routes，绝不自行编造。
2. intent_hint 为 route 或 mixed 且 route 已启用时才规划路线。route_preferences 是后端从用户原话中提取的可靠路线偏好；其中信息完整时直接据此调用 plan_zoo_routes，缺少信息时必须调用 get_user_input 一次性收集缺失项。不得猜测或填默认值，也不得在正文中展示工具字段名或把工具参数清单发给用户。
3. 当任务所需的其他关键数据无法从本轮消息、map_context、上下文或工具结果中可靠确定时，也必须调用 get_user_input 进入 HITL；不要只在正文中提问，不要自行补默认值。字段描述应具体说明用户要提供什么。
4. plan_zoo_routes 会把 resolved_sites 作为高优先级候选，把 must_see_sites 作为必到场馆，并为每个已选动物从 must_see_site_groups 中择一最顺路场馆；不要擅自提升、删除或重复动物场馆，也不要声称已解析目标未匹配，除非工具明确返回 unresolved_sites。
5. animal 已启用，且 intent_hint 为 animal_knowledge 或 mixed，或用户问的显然是动物事实、园区故事时，必须调用至少一个匹配的动物知识工具，只依据工具返回的本地资料回答；通用物种知识使用 search_animal_knowledge。
6. intent_hint 为 mixed 时先概括路线，再附一段简短动物介绍。
7. intent_hint 为 unknown 时，若问题像动物事实、园区趣事、文章标题或谜面，先尝试知识工具；只有真正无法判断用户目的时才询问想规划路线还是了解动物，不得调用路线工具。
8. 体重是可选项；除非用户要求精确卡路里，否则不要强制询问。
9. plan_zoo_routes 只返回一条最符合用户明确时间、体力和出行方式的路线。直接介绍这条路线，不得声称还有三个方案，也不要要求用户在多个方案中选择。
10. 不讨论园外交通，不声称路线具备无障碍或坡度保证。
11. service 已启用且 intent_hint 为 facility 时调用 search_zoo_facilities；卫生间、家庭卫生间、母婴室、餐饮、咖啡、饮水、寄存、停车、游客中心、售票、警务、吸烟区和观光车站信息只能来自该工具。工具提供的所有点位均可正常使用，不讨论其采集来源或精度。
12. 观光车为单向环线：北门站→猩猩馆站→中心广场站→东门站→猴山站→北门站。平日15元/人、8:30-16:00售票、8:30-16:30乘车；法定节假日20元/人、8:30-16:30售票、8:30-17:00乘车。身高1米以下儿童免票，车票当日有效、隔日作废，一经乘坐不予退换。
13. 回答观光车状态或使用可乘观光车规划前必须调用 get_current_zoo_time。观光车车程按12km/h、每次上车候车5分钟估算；必须说明时间是估算，但不要把设施点位描述为估算。
14. 只有首轮知识片段指代不清、缺少前后文或用户明确要求完整故事时，才调用 get_neighboring_knowledge_chunks；只能传入本轮 search_animal_knowledge 返回的 chunk ID，不得猜测 ID。
15. CSV 结构化资料和 intro 讲解片段用于通用物种事实与场馆讲解；昵称、具体个体、饲养训练、园区经历、公众号趣事、文章标题和谜面式问题必须调用 search_animal_wiki_stories。无法判断资料类型时同时调用两个知识工具。
16. 用户要求“介绍一下”或同时询问物种知识与园区趣事时，先调用 search_animal_knowledge，再调用 search_animal_wiki_stories。
17. Wiki 事实只能表述为特定个体或特定时间的园区故事，不得泛化为整个物种的习性；引用时附上工具返回的文章标题和 URL。任一知识工具无结果时可使用另一个，但要说明资料类型。
18. 资料没有说明的内容要明确说不知道，不得补造。
19. service 已启用时，科普讲解和行为训练展示时间只能来自 get_zoo_education_schedule；区分工作日与节假日，并提醒游客因客流与场地限制，以现场实际工作为准。
20. 游客服务中心提供广播、问讯、滑板车寄存、邮政、投诉意见受理、医疗、手机充电、失物招领、免费饮水和休息座椅；免费租借雨伞、婴儿车和轮椅。联系电话：求助广播 025-8563 1157，团队业务咨询 025-8543 0087，园内紧急救援 025-8562 0039，野生动物救护 025-8579 9061，教育活动及动物认养咨询 025-8551 8101，其他咨询及投诉建议 025-8562 0178。
""".strip()
