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
    preferred_fraction: float
    extra_limit: int | None


_PROFILES = (
    _Profile(
        "easy", "轻松逛", 0.60, "少走一点，把时间留给最想看的动物。", 0.50, 0
    ),
    _Profile(
        "balanced", "均衡逛", 0.85, "优先串联已选场馆，再加入少量顺路邻居。", 1.00, 2
    ),
    _Profile(
        "full", "尽兴逛", 1.00, "尽量用足今天的时间，多认识几位动物邻居。", 1.00, None
    ),
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
        preferred = list(dict.fromkeys(site for site in selected_sites if site in point_by_site))
        mandatory = list(dict.fromkeys(site for site in must_see_sites if site in point_by_site))
        if not point_by_site:
            raise RoutePlanningError("地图上还没有可用于规划的场馆点位")
        start = origin or guide.default_origin or MapNamedLocation(
            name="红山森林动物园入口",
            longitude=guide.center.longitude,
            latitude=guide.center.latitude,
        )
        results: list[RouteOption] = []
        signatures: set[tuple[str, ...]] = set()
        for profile in _PROFILES:
            target_minutes = max(30, round(available_minutes * profile.fraction))
            distance_limit = round(ENERGY_LIMITS[energy_level] * profile.fraction)
            sites = self._pick_sites(
                start,
                list(point_by_site.values()),
                preferred,
                mandatory,
                profile,
                target_minutes,
                distance_limit,
            )
            route = self._route_option(
                profile,
                start,
                sites,
                target_minutes,
                distance_limit,
                weight_kg,
            )
            route = self._trim_to_budget(
                route,
                profile,
                start,
                sites,
                set(mandatory),
                set(preferred),
                target_minutes,
                distance_limit,
                weight_kg,
            )
            signature = tuple(route.sites)
            if signature not in signatures:
                signatures.add(signature)
                results.append(route)
        return results

    def _pick_sites(
        self,
        origin: MapLocation,
        points: list[MapPoint],
        preferred_sites: list[str],
        mandatory_sites: list[str],
        profile: _Profile,
        target_minutes: int,
        distance_limit: int,
    ) -> list[MapPoint]:
        point_by_site = {point.site: point for point in points}
        mandatory_names = set(mandatory_sites)
        preferred_names = set(preferred_sites)
        mandatory = [point_by_site[site] for site in mandatory_sites]
        preferred = [
            point_by_site[site]
            for site in preferred_sites
            if site not in mandatory_names
        ]
        ordinary = [
            point
            for point in points
            if point.site not in mandatory_names | preferred_names
        ]
        chosen = _nearest_neighbor(origin, mandatory) if mandatory else []

        preferred_limit = (
            max(1, math.ceil(len(preferred) * profile.preferred_fraction))
            if preferred
            else 0
        )
        chosen = _add_best_candidates(
            origin,
            chosen,
            preferred,
            preferred_limit,
            target_minutes,
            distance_limit,
            preferred=True,
        )
        chosen = _add_best_candidates(
            origin,
            chosen,
            ordinary,
            profile.extra_limit,
            target_minutes,
            distance_limit,
            preferred=False,
        )
        if chosen:
            return _two_opt(origin, chosen)
        fallback = min(
            preferred or ordinary or points,
            key=lambda point: _distance(origin, point),
        )
        return [fallback]

    def _trim_to_budget(
        self,
        route: RouteOption,
        profile: _Profile,
        origin: MapNamedLocation,
        sites: list[MapPoint],
        mandatory: set[str],
        preferred: set[str],
        target_minutes: int,
        distance_limit: int,
        weight_kg: float | None,
    ) -> RouteOption:
        """Drop low-priority optional stops when AMap measurements exceed the estimate."""

        current = list(sites)
        while (
            (route.total_minutes > target_minutes or route.distance_meters > distance_limit)
            and len(current) > 1
        ):
            removable = [point for point in current if point.site not in mandatory]
            if not removable:
                break
            victim = min(
                removable,
                key=lambda point: (
                    point.site in preferred,
                    _candidate_value(point, point.site in preferred),
                    point.site,
                ),
            )
            current.remove(victim)
            current = _two_opt(origin, current)
            route = self._route_option(
                profile,
                origin,
                current,
                target_minutes,
                distance_limit,
                weight_kg,
            )
        return route

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


def _add_best_candidates(
    origin: MapLocation,
    chosen: list[MapPoint],
    candidates: list[MapPoint],
    limit: int | None,
    target_minutes: int,
    distance_limit: int,
    *,
    preferred: bool,
) -> list[MapPoint]:
    """Greedily solve a small orienteering problem using insertion cost as edge weight."""

    route = list(chosen)
    remaining = list(candidates)
    added = 0
    while remaining and (limit is None or added < limit):
        feasible: list[tuple[float, str, list[MapPoint], MapPoint]] = []
        current_distance = _estimated_distance(origin, route)
        for point in remaining:
            proposal = _best_insertion(origin, route, point)
            distance = _estimated_distance(origin, proposal)
            if not _within_estimated_budget(
                origin, proposal, target_minutes, distance_limit
            ):
                continue
            added_distance = max(1, distance - current_distance)
            value = _candidate_value(point, preferred)
            feasible.append((value / added_distance, point.site, proposal, point))
        if not feasible:
            break
        _, _, route, selected = max(feasible, key=lambda item: (item[0], item[1]))
        remaining.remove(selected)
        added += 1
    return route


def _best_insertion(
    origin: MapLocation,
    route: list[MapPoint],
    point: MapPoint,
) -> list[MapPoint]:
    proposals = [
        route[:index] + [point] + route[index:]
        for index in range(len(route) + 1)
    ]
    return min(proposals, key=lambda proposal: _estimated_distance(origin, proposal))


def _within_estimated_budget(
    origin: MapLocation,
    route: list[MapPoint],
    target_minutes: int,
    distance_limit: int,
) -> bool:
    distance = _estimated_distance(origin, route)
    visiting = sum(_visit_minutes(point.animal_count) for point in route)
    return distance <= distance_limit and visiting + distance / 70 <= target_minutes


def _candidate_value(point: MapPoint, preferred: bool) -> float:
    priority = 4.0 if preferred else 1.0
    return priority + math.log1p(max(1, point.animal_count)) / 3


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
