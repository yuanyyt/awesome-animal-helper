"""Deterministic zoo itinerary planning backed by AMap walking routes."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .amap_client import AmapClient, AmapServiceError
from .schemas import (
    MapGuideResponse,
    MapLocation,
    MapNamedLocation,
    MapPoint,
    RouteLeg,
    RouteOption,
)

ENERGY_LIMITS = {"轻松": 1_500, "一般": 3_000, "充沛": 5_000}


class RoutePlanningError(RuntimeError):
    """Raised when no valid itinerary can be built."""


@dataclass(frozen=True)
class _Profile:
    identifier: str
    name: str
    fraction: float
    description: str


_PROFILES = (
    _Profile("easy", "轻松逛", 0.60, "少走一点，把时间留给最想看的动物。"),
    _Profile("balanced", "均衡逛", 0.85, "在步行距离和场馆覆盖之间取得平衡。"),
    _Profile("full", "尽兴逛", 1.00, "尽量用足今天的时间，多认识几位动物邻居。"),
)


class RoutePlanner:
    """Build three transparent, constraint-aware walking itineraries."""

    def __init__(self, amap: AmapClient) -> None:
        self.amap = amap

    def plan(
        self,
        *,
        guide: MapGuideResponse,
        selected_sites: list[str],
        must_see_sites: list[str],
        available_minutes: int,
        energy_level: str,
        origin: MapNamedLocation | None = None,
        weight_kg: float | None = None,
    ) -> list[RouteOption]:
        if available_minutes < 30:
            raise RoutePlanningError("可用时间至少需要 30 分钟")
        if energy_level not in ENERGY_LIMITS:
            raise RoutePlanningError("体力状态应为轻松、一般或充沛")
        point_by_site = {point.site: point for point in guide.points}
        requested = list(dict.fromkeys(site for site in selected_sites if site in point_by_site))
        candidates = requested or list(point_by_site)
        if not candidates:
            raise RoutePlanningError("地图上还没有可用于规划的场馆点位")
        must_see = [site for site in dict.fromkeys(must_see_sites) if site in candidates]
        start = origin or guide.default_origin or MapNamedLocation(
            name="红山森林动物园入口",
            longitude=guide.center.longitude,
            latitude=guide.center.latitude,
        )
        ordered = _nearest_neighbor(start, [point_by_site[site] for site in candidates])
        results: list[RouteOption] = []
        for profile in _PROFILES:
            target_minutes = max(30, round(available_minutes * profile.fraction))
            distance_limit = round(ENERGY_LIMITS[energy_level] * profile.fraction)
            sites = self._pick_sites(
                start,
                ordered,
                must_see,
                target_minutes,
                distance_limit,
            )
            results.append(
                self._route_option(
                    profile,
                    start,
                    sites,
                    target_minutes,
                    distance_limit,
                    weight_kg,
                )
            )
        return results

    def _pick_sites(
        self,
        origin: MapLocation,
        ordered: list[MapPoint],
        must_see: list[str],
        target_minutes: int,
        distance_limit: int,
    ) -> list[MapPoint]:
        chosen = [point for point in ordered if point.site in must_see]
        remaining = [point for point in ordered if point.site not in must_see]
        for point in remaining:
            proposal = _nearest_neighbor(origin, [*chosen, point])
            distance = _estimated_distance(origin, proposal)
            visiting = sum(_visit_minutes(item.animal_count) for item in proposal)
            if distance <= distance_limit and visiting + distance / 70 <= target_minutes:
                chosen = proposal
        return _nearest_neighbor(origin, chosen or [ordered[0]])

    def _route_option(
        self,
        profile: _Profile,
        origin: MapNamedLocation,
        sites: list[MapPoint],
        target_minutes: int,
        distance_limit: int,
        weight_kg: float | None,
    ) -> RouteOption:
        legs: list[RouteLeg] = []
        current: MapLocation = origin
        current_name = origin.name
        for point in sites:
            try:
                if _same_location(current, point):
                    distance, duration, steps, polyline = 0, 0, [], [current, point]
                else:
                    route = self.amap.walking_route(current, point)
                    distance = route.distance_meters
                    duration = route.duration_seconds
                    steps = list(route.steps)
                    polyline = list(route.polyline)
            except AmapServiceError as exc:
                raise RoutePlanningError(f"无法规划到「{point.site}」的步行路线") from exc
            legs.append(
                RouteLeg(
                    from_name=current_name,
                    to_name=point.site,
                    distance_meters=distance,
                    duration_seconds=duration,
                    steps=steps,
                    polyline=polyline,
                )
            )
            current, current_name = point, point.site

        distance = sum(leg.distance_meters for leg in legs)
        walking_minutes = math.ceil(sum(leg.duration_seconds for leg in legs) / 60)
        visiting_minutes = sum(_visit_minutes(point.animal_count) for point in sites)
        total_minutes = walking_minutes + visiting_minutes
        warnings: list[str] = []
        if total_minutes > target_minutes:
            warnings.append(f"高德实测后约超出该方案目标 {total_minutes - target_minutes} 分钟")
        if distance > distance_limit:
            warnings.append(f"高德实测距离比体力目标多 {distance - distance_limit} 米")
        has_stairs = any(step.walk_type == "21" for leg in legs for step in leg.steps)
        if has_stairs:
            warnings.append("路线包含高德标记的阶梯路段")
        calories, calories_range = _calories(sum(leg.duration_seconds for leg in legs), weight_kg)
        polyline = [point for leg in legs for point in leg.polyline]
        return RouteOption(
            id=profile.identifier,
            name=profile.name,
            description=profile.description,
            sites=[point.site for point in sites],
            distance_meters=distance,
            walking_minutes=walking_minutes,
            visiting_minutes=visiting_minutes,
            total_minutes=total_minutes,
            calories_kcal=calories,
            calories_range_kcal=calories_range,
            has_stairs=has_stairs,
            warnings=warnings,
            legs=legs,
            polyline=polyline,
        )


def _nearest_neighbor(origin: MapLocation, points: list[MapPoint]) -> list[MapPoint]:
    remaining = list(points)
    ordered: list[MapPoint] = []
    current = origin
    while remaining:
        next_point = min(remaining, key=lambda point: _distance(current, point))
        remaining.remove(next_point)
        ordered.append(next_point)
        current = next_point
    return _two_opt(origin, ordered)


def _two_opt(origin: MapLocation, points: list[MapPoint]) -> list[MapPoint]:
    """Remove obvious route crossings while keeping the first stop unconstrained."""

    best = list(points)
    best_distance = _estimated_distance(origin, best)
    improved = True
    while improved:
        improved = False
        for start in range(len(best) - 1):
            for end in range(start + 2, len(best) + 1):
                candidate = best[:start] + list(reversed(best[start:end])) + best[end:]
                candidate_distance = _estimated_distance(origin, candidate)
                if candidate_distance < best_distance:
                    best, best_distance, improved = candidate, candidate_distance, True
    return best


def _estimated_distance(origin: MapLocation, points: list[MapPoint]) -> int:
    total = 0.0
    current = origin
    for point in points:
        total += _distance(current, point) * 1.25
        current = point
    return round(total)


def _distance(first: MapLocation, second: MapLocation) -> float:
    latitude = math.radians((first.latitude + second.latitude) / 2)
    x = math.radians(second.longitude - first.longitude) * math.cos(latitude)
    y = math.radians(second.latitude - first.latitude)
    return math.hypot(x, y) * 6_371_000


def _same_location(first: MapLocation, second: MapLocation) -> bool:
    return _distance(first, second) < 2


def _visit_minutes(animal_count: int) -> int:
    return round(min(35, max(15, 10 + 5 * math.sqrt(max(1, animal_count)))))


def _calories(duration_seconds: int, weight_kg: float | None) -> tuple[int | None, tuple[int, int] | None]:
    hours = duration_seconds / 3600
    if weight_kg is not None and 30 <= weight_kg <= 200:
        return round(3.3 * weight_kg * hours), None
    return None, (round(3.3 * 50 * hours), round(3.3 * 80 * hours))
