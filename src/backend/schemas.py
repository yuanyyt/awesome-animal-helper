"""Public API schemas for animal data, maps, and guided route planning."""

from typing import Literal

from pydantic import BaseModel, Field


class SiteSummary(BaseModel):
    """A venue and the number of animals assigned to it."""

    name: str
    animal_count: int


class AnimalDetail(BaseModel):
    """Normalized animal information exposed to the frontend."""

    name: str
    scientific_name: str | None = None
    taxonomy: str | None = None
    habitat: str | None = None
    distribution: str | None = None
    diet: str | None = None
    behavior: str | None = None
    reproduction: str | None = None
    conservation_status: str | None = None
    fun_facts: list[str]
    source_url: str | None = None
    language: str | None = None
    data_status: str
    sites: list[str]


class AnimalListResponse(BaseModel):
    """Response envelope shared by list, search, and exact lookup requests."""

    items: list[AnimalDetail]
    sites: list[SiteSummary]
    total: int
    filtered_count: int


class MapLocation(BaseModel):
    """One GCJ-02 coordinate returned by AMap."""

    longitude: float
    latitude: float


class MapPoint(MapLocation):
    """A zoo venue matched to an AMap POI."""

    site: str
    poi_name: str
    address: str
    animal_count: int


class MapNamedLocation(MapLocation):
    """A named map location, such as the default zoo entrance."""

    name: str


class MapJsConfig(BaseModel):
    """Public JS API configuration; the security code stays server-side."""

    api_key: str
    service_host: str


class MapGuideResponse(BaseModel):
    """Map configuration consumed by the Vue guide."""

    center: MapLocation
    zoom: int
    image_url: str
    points: list[MapPoint]
    provider: str
    js_api: MapJsConfig | None = None
    default_origin: MapNamedLocation | None = None


class RouteStep(BaseModel):
    """One human-readable walking instruction from AMap."""

    instruction: str
    distance_meters: int
    duration_seconds: int
    walk_type: str | None = None


class RouteLeg(BaseModel):
    """A routed segment between two named places."""

    from_name: str
    to_name: str
    distance_meters: int
    duration_seconds: int
    steps: list[RouteStep]
    polyline: list[MapLocation]


class RouteOption(BaseModel):
    """One complete zoo itinerary returned to the frontend."""

    id: str
    name: str
    description: str
    sites: list[str]
    distance_meters: int
    walking_minutes: int
    visiting_minutes: int
    total_minutes: int
    calories_kcal: int | None = None
    calories_range_kcal: tuple[int, int] | None = None
    has_stairs: bool = False
    warnings: list[str] = Field(default_factory=list)
    legs: list[RouteLeg]
    polyline: list[MapLocation]


class GuideMapContext(BaseModel):
    """Map selections included with a chat request."""

    selected_sites: list[str] = Field(default_factory=list, max_length=30)
    origin: MapNamedLocation | None = None


class GuideChatRequest(BaseModel):
    """A new visitor message and its current map context."""

    session_id: str | None = Field(default=None, max_length=100)
    message: str = Field(min_length=1, max_length=1000)
    map_context: GuideMapContext = Field(default_factory=GuideMapContext)


class GuideContinueRequest(BaseModel):
    """Values used to resolve one paused Agno HITL run."""

    session_id: str = Field(min_length=1, max_length=100)
    values: dict[str, str | int | float | bool]


class GuideInputField(BaseModel):
    """A serializable Agno user-input requirement."""

    name: str
    field_type: str
    description: str
    value: str | int | float | bool | list[str] | None = None


class GuideChatResponse(BaseModel):
    """A completed or paused guide-agent turn."""

    session_id: str
    run_id: str
    status: Literal["completed", "input_required"]
    assistant_message: str
    intent: Literal["route", "animal_knowledge", "mixed", "unknown"] = "unknown"
    resolved_sites: list[str] = Field(default_factory=list)
    unresolved_terms: list[str] = Field(default_factory=list)
    knowledge_items: list[AnimalDetail] = Field(default_factory=list)
    required_inputs: list[GuideInputField] = Field(default_factory=list)
    route_options: list[RouteOption] = Field(default_factory=list)
