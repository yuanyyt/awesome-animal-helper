"""Load the checked-in OpenStreetMap boundary snapshot."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from src.backend.domain.models import MapLocation

BOUNDARY_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "hongshan_zoo_boundary.geojson"
)
OSM_WAY_ID = 62344632


class OsmBoundaryError(ValueError):
    """Raised when the checked-in OSM snapshot is missing or malformed."""


@dataclass(frozen=True)
class OsmBoundarySnapshot:
    points: tuple[MapLocation, ...]
    source: str
    source_url: str
    attribution: str
    object_type: str
    object_id: int


def load_osm_boundary(path: Path = BOUNDARY_PATH) -> OsmBoundarySnapshot:
    """Read and validate the exact closed polygon for OSM way 62344632."""

    try:
        feature = json.loads(path.read_text(encoding="utf-8"))
        properties = feature["properties"]
        geometry = feature["geometry"]
        coordinates = geometry["coordinates"][0]
    except (OSError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise OsmBoundaryError("OSM 园区边界快照无法读取") from exc

    if (
        feature.get("type") != "Feature"
        or geometry.get("type") != "Polygon"
        or properties.get("osm_type") != "way"
        or properties.get("osm_id") != OSM_WAY_ID
    ):
        raise OsmBoundaryError("OSM 园区边界对象不匹配")

    points = tuple(_parse_point(value) for value in coordinates)
    if len(points) < 4 or points[0] != points[-1]:
        raise OsmBoundaryError("OSM 园区边界必须是闭合多边形")

    return OsmBoundarySnapshot(
        points=points,
        source=_required_text(properties, "source"),
        source_url=_required_text(properties, "source_url"),
        attribution=_required_text(properties, "attribution"),
        object_type="way",
        object_id=OSM_WAY_ID,
    )


def _parse_point(value: object) -> MapLocation:
    try:
        longitude, latitude = value  # type: ignore[misc]
        longitude = float(longitude)
        latitude = float(latitude)
    except (TypeError, ValueError) as exc:
        raise OsmBoundaryError("OSM 园区边界包含无效坐标") from exc
    if not (
        math.isfinite(longitude)
        and math.isfinite(latitude)
        and -180 <= longitude <= 180
        and -90 <= latitude <= 90
    ):
        raise OsmBoundaryError("OSM 园区边界包含越界坐标")
    return MapLocation(longitude=longitude, latitude=latitude)


def _required_text(properties: dict, key: str) -> str:
    value = str(properties.get(key, "")).strip()
    if not value:
        raise OsmBoundaryError(f"OSM 园区边界缺少 {key}")
    return value
