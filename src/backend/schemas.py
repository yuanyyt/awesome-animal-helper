"""Public API schemas for animal guide data."""

from pydantic import BaseModel


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
