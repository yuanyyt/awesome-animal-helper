"""Secure AMap Web Service integration for the zoo guide map."""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import httpx
from dotenv import load_dotenv

from src.backend.domain.models import (
    MapBoundary,
    MapGuideResponse,
    MapJsConfig,
    MapLocation,
    MapNamedLocation,
    MapPoint,
    RouteStep,
    SiteSummary,
)
from src.backend.integrations.amap.boundary import load_osm_boundary
from src.backend.services.zoo_services import (
    facility_category,
    public_facilities,
    shuttle_service,
)

POI_TEXT_URL = "https://restapi.amap.com/v5/place/text"
POI_AROUND_URL = "https://restapi.amap.com/v5/place/around"
STATIC_MAP_URL = "https://restapi.amap.com/v3/staticmap"
WALKING_ROUTE_URL = "https://restapi.amap.com/v3/direction/walking"
COORDINATE_CONVERT_URL = "https://restapi.amap.com/v3/assistant/coordinate/convert"

ZOO_KEYWORD = "南京红山森林动物园"
ZOO_REGION = "南京"
MAP_ZOOM = 16
MAP_WIDTH = 1024
MAP_HEIGHT = 640
AMAP_JS_PROXY_PATH = "/_AMapService"

_SITE_ALIASES: dict[str, tuple[str, ...]] = {
    "大熊猫": ("南京熊猫馆", "金陵中华大熊猫苑"),
    "犀鸟雨林": ("热带鸟馆",),
    "非洲之歌": ("非洲之歌",),
    "澳洲袋鼠角": ("澳洲区", "袋鼠"),
    "狐猴之家": ("狐猴岛", "狐猴"),
    "本土物种保育区": ("本土物种保育区",),
    "细尾獴": ("细尾獴馆",),
    "狼": ("新狼馆",),
    "猫科星球": ("中国猫科馆",),
    "中国猫科": ("中国猫科馆",),
    "虎": ("新虎园", "狮虎山"),
    "熊馆": ("熊馆",),
    "猕猴世界": ("猴山",),
    "高黎贡": ("高黎贡展区",),
    "考拉": ("考拉馆",),
    "象": ("大象馆",),
    "亚洲灵长区": ("亚洲灵长区",),
    "冈瓦纳": ("冈瓦纳",),
}


class AmapServiceError(RuntimeError):
    """Raised when AMap rejects a request or returns malformed data."""


@dataclass(frozen=True)
class _Poi:
    name: str
    longitude: float
    latitude: float
    address: str
    distance: int | None = None
    typecode: str = ""


@dataclass(frozen=True)
class WalkingRoute:
    """Normalized walking path returned by AMap."""

    distance_meters: int
    duration_seconds: int
    steps: tuple[RouteStep, ...]
    polyline: tuple[MapLocation, ...]


class AmapClient:
    """Resolve zoo POIs and proxy static maps without exposing the API key."""

    def __init__(
        self,
        api_key: str,
        *,
        js_api_key: str = "",
        security_key: str = "",
        timeout: float = 20.0,
        retries: int = 2,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise ValueError("AMAP_WEBSERVICE_KEY 不能为空")
        self.api_key = api_key
        self.js_api_key = js_api_key.strip()
        self.security_key = security_key.strip()
        self.retries = max(0, retries)
        self._sleep = sleep
        self._owns_http = http is None
        self.http = http or httpx.Client(timeout=timeout, follow_redirects=True)
        self._guide_cache: dict[tuple[tuple[str, int], ...], MapGuideResponse] = {}
        self._image_cache: tuple[bytes, str] | None = None
        self._walking_cache: dict[tuple[float, float, float, float], WalkingRoute] = {}
        self._boundary_cache: MapBoundary | None = None

    @classmethod
    def from_env(cls) -> AmapClient:
        load_dotenv()
        return cls(
            os.getenv("AMAP_WEBSERVICE_KEY", ""),
            js_api_key=os.getenv("JS_API_KEY", ""),
            security_key=os.getenv("SECURITY_KEY", ""),
        )

    @property
    def js_api_enabled(self) -> bool:
        return bool(self.js_api_key and self.security_key)

    def close(self) -> None:
        if self._owns_http:
            self.http.close()

    def build_guide(self, sites: Iterable[SiteSummary]) -> MapGuideResponse:
        site_list = list(sites)
        cache_key = tuple((site.name, site.animal_count) for site in site_list)
        if cache_key in self._guide_cache:
            return self._guide_cache[cache_key]

        center_poi = self._search_zoo()
        nearby = self._search_nearby(center_poi)
        facility_pois = self._search_facilities(center_poi)
        points = self._match_sites(site_list, nearby)
        boundary = self._build_boundary()
        facility_pois = [
            poi
            for poi in facility_pois
            if facility_category(poi.name, poi.typecode)
            in {"metro", "bus_terminal", "train_station", "parking", "entrance"}
            or _point_in_boundary(poi, boundary.points)
        ]
        guide = MapGuideResponse(
            center=MapLocation(
                longitude=center_poi.longitude,
                latitude=center_poi.latitude,
            ),
            zoom=MAP_ZOOM,
            image_url="/api/map/image",
            points=points,
            facilities=public_facilities(facility_pois),
            shuttle=shuttle_service(),
            boundary=boundary,
            provider="高德地图",
            default_origin=self._default_origin(center_poi, nearby),
            js_api=(
                MapJsConfig(
                    api_key=self.js_api_key,
                    service_host=AMAP_JS_PROXY_PATH,
                )
                if self.js_api_enabled
                else None
            ),
        )
        self._guide_cache[cache_key] = guide
        return guide

    def convert_gps_coordinates(
        self, points: Iterable[MapLocation]
    ) -> list[MapLocation]:
        """Convert WGS-84 GPS coordinates to GCJ-02 in API-sized batches."""

        source = list(points)
        converted: list[MapLocation] = []
        for start in range(0, len(source), 40):
            batch = source[start : start + 40]
            data = self._get_json(
                COORDINATE_CONVERT_URL,
                {
                    "locations": "|".join(
                        f"{point.longitude:.6f},{point.latitude:.6f}"
                        for point in batch
                    ),
                    "coordsys": "gps",
                    "output": "JSON",
                },
            )
            batch_result = _parse_converted_locations(data)
            if len(batch_result) != len(batch):
                raise AmapServiceError("高德坐标转换返回点数不一致")
            converted.extend(batch_result)
        return converted

    def _build_boundary(self) -> MapBoundary:
        if self._boundary_cache is not None:
            return self._boundary_cache
        snapshot = load_osm_boundary()
        converted = self.convert_gps_coordinates(snapshot.points[:-1])
        if len(converted) < 3:
            raise AmapServiceError("OSM 园区边界没有足够坐标")
        self._boundary_cache = MapBoundary(
            points=[*converted, converted[0]],
            source=snapshot.source,
            source_url=snapshot.source_url,
            attribution=snapshot.attribution,
            object_type="way",
            object_id=snapshot.object_id,
        )
        return self._boundary_cache

    def walking_route(self, origin: MapLocation, destination: MapLocation) -> WalkingRoute:
        """Return and cache one AMap walking route between GCJ-02 coordinates."""

        cache_key = (
            round(origin.longitude, 6),
            round(origin.latitude, 6),
            round(destination.longitude, 6),
            round(destination.latitude, 6),
        )
        if cached := self._walking_cache.get(cache_key):
            return cached
        data = self._get_json(
            WALKING_ROUTE_URL,
            {
                "origin": f"{origin.longitude:.6f},{origin.latitude:.6f}",
                "destination": f"{destination.longitude:.6f},{destination.latitude:.6f}",
                "output": "JSON",
            },
        )
        route = _parse_walking_route(data)
        self._walking_cache[cache_key] = route
        return route

    def static_map(self, center: MapLocation) -> tuple[bytes, str]:
        if self._image_cache is not None:
            return self._image_cache
        response = self.http.get(
            STATIC_MAP_URL,
            params={
                "key": self.api_key,
                "location": f"{center.longitude:.6f},{center.latitude:.6f}",
                "zoom": MAP_ZOOM,
                "size": f"{MAP_WIDTH}*{MAP_HEIGHT}",
                "scale": 1,
                "traffic": 0,
            },
        )
        if response.status_code >= 400:
            raise AmapServiceError(f"高德静态地图 HTTP {response.status_code}")
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
        if not content_type.startswith("image/"):
            raise AmapServiceError("高德静态地图返回了非图片内容")
        self._image_cache = response.content, content_type
        return self._image_cache

    def _search_zoo(self) -> _Poi:
        data = self._get_json(
            POI_TEXT_URL,
            {
                "keywords": ZOO_KEYWORD,
                "region": ZOO_REGION,
                "city_limit": "true",
                "page_size": 10,
            },
        )
        pois = _parse_pois(data)
        if not pois:
            raise AmapServiceError("高德未找到南京红山森林动物园")
        return min(
            pois,
            key=lambda poi: (poi.name not in {"红山森林动物园", ZOO_KEYWORD}, len(poi.name)),
        )

    def _search_nearby(self, center: _Poi) -> list[_Poi]:
        found: list[_Poi] = []
        for page in (1, 2, 3):
            if page > 1:
                self._sleep(1.05)
            data = self._get_json(
                POI_AROUND_URL,
                {
                    "location": f"{center.longitude:.6f},{center.latitude:.6f}",
                    "radius": 1200,
                    "types": "110000",
                    "sortrule": "distance",
                    "region": ZOO_REGION,
                    "page_size": 25,
                    "page_num": page,
                },
            )
            found.extend(_parse_pois(data))
        return list({(poi.name, poi.longitude, poi.latitude): poi for poi in found}.values())

    def _search_facilities(self, center: _Poi) -> list[_Poi]:
        """Fetch broad visitor-service POIs; curated map points fill any gaps."""

        found: list[_Poi] = []
        for page in (1, 2, 3):
            if page > 1:
                self._sleep(1.05)
            data = self._get_json(
                POI_AROUND_URL,
                {
                    "location": f"{center.longitude:.6f},{center.latitude:.6f}",
                    "radius": 1200,
                    "types": "050000|060000|070000|110000|150000|160000|170000|200000",
                    "sortrule": "distance",
                    "region": ZOO_REGION,
                    "page_size": 25,
                    "page_num": page,
                },
            )
            found.extend(_parse_pois(data))
        return list({(poi.name, poi.longitude, poi.latitude): poi for poi in found}.values())

    @staticmethod
    def _default_origin(center: _Poi, nearby: list[_Poi]) -> MapNamedLocation:
        entrances = [poi for poi in nearby if "门" in poi.name and "红山" in poi.name]
        south_entrances = [
            poi
            for poi in nearby
            if "南门" in _normalize(poi.name)
            and ("红山" in _normalize(poi.name) or "新区" in _normalize(poi.name))
        ]
        candidates = south_entrances or entrances
        entrance = (
            min(candidates, key=lambda poi: poi.distance or 999_999)
            if candidates
            else center
        )
        return MapNamedLocation(
            name=(
                "南门新区"
                if south_entrances
                else entrance.name if entrance is not center else "红山森林动物园入口"
            ),
            longitude=entrance.longitude,
            latitude=entrance.latitude,
        )

    def _get_json(self, url: str, params: dict[str, object]) -> dict:
        for attempt in range(self.retries + 1):
            response = self.http.get(url, params={"key": self.api_key, **params})
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < self.retries:
                    self._sleep(1.05 * 2**attempt)
                    continue
            if response.status_code >= 400:
                raise AmapServiceError(f"高德 Web Service HTTP {response.status_code}")
            try:
                data = response.json()
            except ValueError as exc:
                raise AmapServiceError("高德返回了无效 JSON") from exc
            if data.get("status") == "1":
                return data
            info = str(data.get("info", "UNKNOWN"))
            if ("LIMIT" in info or "CUQPS" in info) and attempt < self.retries:
                self._sleep(1.05 * 2**attempt)
                continue
            raise AmapServiceError(
                f"高德请求失败：{info} ({data.get('infocode', '')})"
            )
        raise AmapServiceError("高德请求重试后仍失败")

    @staticmethod
    def _match_sites(sites: list[SiteSummary], pois: list[_Poi]) -> list[MapPoint]:
        points: list[MapPoint] = []
        for site in sites:
            aliases = _SITE_ALIASES.get(site.name, (site.name,))
            candidates = [
                poi
                for poi in pois
                if any(_normalize(alias) in _normalize(poi.name) for alias in aliases)
            ]
            if not candidates:
                continue
            poi = min(candidates, key=lambda item: (item.distance or 0, len(item.name)))
            points.append(
                MapPoint(
                    site=site.name,
                    poi_name=poi.name,
                    longitude=poi.longitude,
                    latitude=poi.latitude,
                    address=poi.address,
                    animal_count=site.animal_count,
                )
            )
        return points


def _parse_pois(data: dict) -> list[_Poi]:
    pois: list[_Poi] = []
    for raw in data.get("pois", []):
        try:
            longitude, latitude = (float(value) for value in raw["location"].split(",", 1))
        except (KeyError, TypeError, ValueError):
            continue
        distance_text = str(raw.get("distance", ""))
        pois.append(
            _Poi(
                name=str(raw.get("name", "")).strip(),
                longitude=longitude,
                latitude=latitude,
                address=str(raw.get("address", "")).strip(),
                distance=int(distance_text) if distance_text.isdigit() else None,
                typecode=str(raw.get("typecode", "")).strip(),
            )
        )
    return [poi for poi in pois if poi.name]


def _normalize(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", value).casefold()


def _point_in_boundary(point: _Poi, polygon: list[MapLocation]) -> bool:
    inside = False
    for index, current in enumerate(polygon):
        previous = polygon[index - 1]
        crosses = (current.latitude > point.latitude) != (previous.latitude > point.latitude)
        if not crosses:
            continue
        crossing_longitude = (
            (previous.longitude - current.longitude)
            * (point.latitude - current.latitude)
            / (previous.latitude - current.latitude)
            + current.longitude
        )
        if point.longitude < crossing_longitude:
            inside = not inside
    return inside


def _parse_walking_route(data: dict) -> WalkingRoute:
    try:
        path = data["route"]["paths"][0]
        distance = int(float(path["distance"]))
        duration = int(float(path["duration"]))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AmapServiceError("高德未返回可用的步行路线") from exc

    steps: list[RouteStep] = []
    polyline: list[MapLocation] = []
    for raw in path.get("steps", []):
        step_polyline = _parse_polyline(str(raw.get("polyline", "")))
        if step_polyline:
            polyline.extend(step_polyline[1:] if polyline and polyline[-1] == step_polyline[0] else step_polyline)
        steps.append(
            RouteStep(
                instruction=str(raw.get("instruction", "继续步行")).strip() or "继续步行",
                distance_meters=_safe_int(raw.get("distance")),
                duration_seconds=_safe_int(raw.get("duration")),
                walk_type=str(raw.get("walk_type", "")).strip() or None,
            )
        )
    if not polyline:
        raise AmapServiceError("高德步行路线缺少坐标轨迹")
    return WalkingRoute(distance, duration, tuple(steps), tuple(polyline))


def _parse_polyline(value: str) -> list[MapLocation]:
    points: list[MapLocation] = []
    for pair in value.split(";"):
        try:
            longitude, latitude = (float(item) for item in pair.split(",", 1))
        except (TypeError, ValueError):
            continue
        points.append(MapLocation(longitude=longitude, latitude=latitude))
    return points


def _parse_converted_locations(data: dict) -> list[MapLocation]:
    value = str(data.get("locations", "")).strip()
    if not value:
        raise AmapServiceError("高德未返回坐标转换结果")
    return _parse_polyline(value)


def _safe_int(value: object) -> int:
    try:
        return int(float(str(value)))
    except ValueError:
        return 0


def amap_proxy_target(path: str) -> str:
    """Resolve an allow-listed JS API service path to its AMap origin."""
    normalized = path.strip("/")
    if not re.fullmatch(r"v[345]/[A-Za-z0-9_./-]+", normalized):
        raise ValueError("不允许的高德代理路径")
    origin = (
        "https://webapi.amap.com"
        if normalized.startswith("v4/map/styles")
        else "https://restapi.amap.com"
    )
    return f"{origin}/{normalized}"
