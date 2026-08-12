"""Deterministic zoo itinerary planning backed by AMap walking routes."""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.backend.domain.models import (
    MapGuideResponse,
    MapLocation,
    MapNamedLocation,
    MapPoint,
    RouteLeg,
    RouteOption,
    ShuttleService,
    ShuttleStation,
)
from src.backend.integrations.amap.client import AmapClient, AmapServiceError

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
    """Build transparent, constraint-aware zoo itineraries."""

    def __init__(self, amap: AmapClient) -> None:
        self.amap = amap

    def plan(
        self,
        *,
        guide: MapGuideResponse,
        selected_sites: list[str],
        must_see_sites: list[str],
        must_see_site_groups: list[list[str]] | None = None,
        available_minutes: int,
        energy_level: str,
        origin: MapNamedLocation | None = None,
        weight_kg: float | None = None,
        transport_preference: str = "walking",
        shuttle_operating: bool = False,
        shuttle_fare_yuan: int | None = None,
        single_route: bool = False,
    ) -> list[RouteOption]:
        if available_minutes < 30:
            raise RoutePlanningError("可用时间至少需要 30 分钟")
        if energy_level not in ENERGY_LIMITS:
            raise RoutePlanningError("体力状态应为轻松、一般或充沛")
        if transport_preference not in {"walking", "mixed"}:
            raise RoutePlanningError("出行方式应为纯步行或可乘观光车")
        point_by_site = {point.site: point for point in guide.points}
        preferred = list(dict.fromkeys(site for site in selected_sites if site in point_by_site))
        mandatory = list(dict.fromkeys(site for site in must_see_sites if site in point_by_site))
        groups = [
            list(dict.fromkeys(site for site in group if site in point_by_site))
            for group in (must_see_site_groups or [])
        ]
        groups = [group for group in groups if group]
        if not point_by_site:
            raise RoutePlanningError("地图上还没有可用于规划的场馆点位")
        start = origin or guide.default_origin or MapNamedLocation(
            name="红山森林动物园入口",
            longitude=guide.center.longitude,
            latitude=guide.center.latitude,
        )
        results: list[RouteOption] = []
        signatures: set[tuple[str, ...]] = set()
        route_id = {"轻松": "easy", "一般": "balanced", "充沛": "full"}[energy_level]
        profiles = (
            tuple(profile for profile in _PROFILES if profile.identifier == route_id)
            if single_route
            else _PROFILES
        )
        for profile in profiles:
            target_minutes = max(30, round(available_minutes * profile.fraction))
            distance_limit = round(ENERGY_LIMITS[energy_level] * profile.fraction)
            sites = self._pick_sites(
                start,
                list(point_by_site.values()),
                preferred,
                mandatory,
                groups,
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
                guide,
                transport_preference,
                shuttle_operating,
                shuttle_fare_yuan,
            )
            route = self._trim_to_budget(
                route,
                profile,
                start,
                sites,
                set(mandatory) | _group_cover_sites(sites, groups),
                set(preferred),
                target_minutes,
                distance_limit,
                weight_kg,
                guide,
                transport_preference,
                shuttle_operating,
                shuttle_fare_yuan,
            )
            signature = tuple(route.sites)
            if signature not in signatures:
                signatures.add(signature)
                results.append(route)
        return results

    def navigate(
        self,
        *,
        guide: MapGuideResponse,
        destinations: list[MapNamedLocation],
        origin: MapNamedLocation | None = None,
        transport_preference: str = "auto",
        shuttle_operating: bool = False,
        shuttle_fare_yuan: int | None = None,
        weight_kg: float | None = None,
    ) -> RouteOption:
        """Build direct navigation without adding itinerary stops or visit time."""

        if not destinations:
            raise RoutePlanningError("请至少提供一个路线目的地")
        if transport_preference not in {"auto", "walking", "shuttle"}:
            raise RoutePlanningError("导航方式应为自动、纯步行或观光车")
        start = origin or guide.default_origin or MapNamedLocation(
            name="红山森林动物园入口",
            longitude=guide.center.longitude,
            latitude=guide.center.latitude,
        )
        allow_shuttle = transport_preference != "walking" and shuttle_operating
        prefer_shuttle = transport_preference == "shuttle"
        legs: list[RouteLeg] = []
        current: MapLocation = start
        current_name = start.name
        for destination in destinations:
            legs.extend(
                self._travel_legs(
                    current,
                    current_name,
                    destination,
                    destination.name,
                    guide,
                    allow_shuttle,
                    prefer_shuttle=prefer_shuttle,
                    compare_shuttle_by_time=transport_preference == "auto",
                )
            )
            current, current_name = destination, destination.name

        walking_distance = sum(
            leg.distance_meters for leg in legs if leg.mode == "walking"
        )
        walking_seconds = sum(
            leg.duration_seconds for leg in legs if leg.mode == "walking"
        )
        shuttle_seconds = sum(
            leg.duration_seconds for leg in legs if leg.mode == "shuttle"
        )
        uses_shuttle = bool(shuttle_seconds)
        warnings: list[str] = []
        if transport_preference == "shuttle" and not shuttle_operating:
            warnings.append("当前不在观光车运营时段内，本次按纯步行规划")
        if uses_shuttle:
            warnings.append("观光车车程按平均 12 km/h、候车 5 分钟估算")
        has_stairs = any(
            step.walk_type == "21" for leg in legs for step in leg.steps
        )
        if has_stairs:
            warnings.append("路线包含高德标记的阶梯路段")
        calories, calories_range = _calories(walking_seconds, weight_kg)
        walking_minutes = math.ceil(walking_seconds / 60)
        shuttle_minutes = math.ceil(shuttle_seconds / 60)
        mode_name = "观光车接驳导航" if uses_shuttle else "步行导航"
        return RouteOption(
            id="navigation",
            name=mode_name,
            description=f"从{start.name}前往{'、'.join(item.name for item in destinations)}。",
            sites=[item.name for item in destinations],
            distance_meters=sum(leg.distance_meters for leg in legs),
            walking_distance_meters=walking_distance,
            walking_minutes=walking_minutes,
            shuttle_minutes=shuttle_minutes,
            visiting_minutes=0,
            total_minutes=walking_minutes + shuttle_minutes,
            calories_kcal=calories,
            calories_range_kcal=calories_range,
            has_stairs=has_stairs,
            warnings=warnings,
            legs=legs,
            polyline=[point for leg in legs for point in leg.polyline],
            transport_preference="mixed" if uses_shuttle else "walking",
            uses_shuttle=uses_shuttle,
            shuttle_fare_yuan=shuttle_fare_yuan if uses_shuttle else None,
            estimated_wait_minutes=5 if uses_shuttle else 0,
        )

    def _pick_sites(
        self,
        origin: MapLocation,
        points: list[MapPoint],
        preferred_sites: list[str],
        mandatory_sites: list[str],
        mandatory_groups: list[list[str]],
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
        chosen = _cover_required_groups(origin, chosen, mandatory_groups, point_by_site)

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
        guide: MapGuideResponse,
        transport_preference: str,
        shuttle_operating: bool,
        shuttle_fare_yuan: int | None,
    ) -> RouteOption:
        """Drop low-priority optional stops when AMap measurements exceed the estimate."""

        current = list(sites)
        while (
            (
                route.total_minutes > target_minutes
                or (
                    route.walking_distance_meters
                    if route.walking_distance_meters is not None
                    else route.distance_meters
                )
                > distance_limit
            )
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
                guide,
                transport_preference,
                shuttle_operating,
                shuttle_fare_yuan,
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
        guide: MapGuideResponse,
        transport_preference: str,
        shuttle_operating: bool,
        shuttle_fare_yuan: int | None,
    ) -> RouteOption:
        legs: list[RouteLeg] = []
        current: MapLocation = origin
        current_name = origin.name
        for point in sites:
            legs.extend(
                self._travel_legs(
                    current,
                    current_name,
                    point,
                    point.site,
                    guide,
                    transport_preference == "mixed" and shuttle_operating,
                )
            )
            current, current_name = point, point.site

        distance = sum(leg.distance_meters for leg in legs)
        walking_distance = sum(leg.distance_meters for leg in legs if leg.mode == "walking")
        walking_seconds = sum(leg.duration_seconds for leg in legs if leg.mode == "walking")
        shuttle_seconds = sum(leg.duration_seconds for leg in legs if leg.mode == "shuttle")
        walking_minutes = math.ceil(walking_seconds / 60)
        shuttle_minutes = math.ceil(shuttle_seconds / 60)
        visiting_minutes = sum(_visit_minutes(point.animal_count) for point in sites)
        total_minutes = walking_minutes + shuttle_minutes + visiting_minutes
        warnings: list[str] = []
        if total_minutes > target_minutes:
            warnings.append(f"高德实测后约超出该方案目标 {total_minutes - target_minutes} 分钟")
        if walking_distance > distance_limit:
            warnings.append(f"高德实测步行距离比体力目标多 {walking_distance - distance_limit} 米")
        if transport_preference == "mixed" and not shuttle_operating:
            warnings.append("当前不在观光车运营时段内，本方案按纯步行规划")
        if shuttle_seconds:
            warnings.append("观光车车程按平均 12 km/h、候车 5 分钟估算")
        has_stairs = any(step.walk_type == "21" for leg in legs for step in leg.steps)
        if has_stairs:
            warnings.append("路线包含高德标记的阶梯路段")
        calories, calories_range = _calories(walking_seconds, weight_kg)
        polyline = [point for leg in legs for point in leg.polyline]
        return RouteOption(
            id=profile.identifier,
            name=profile.name,
            description=profile.description,
            sites=[point.site for point in sites],
            distance_meters=distance,
            walking_distance_meters=walking_distance,
            walking_minutes=walking_minutes,
            shuttle_minutes=shuttle_minutes,
            visiting_minutes=visiting_minutes,
            total_minutes=total_minutes,
            calories_kcal=calories,
            calories_range_kcal=calories_range,
            has_stairs=has_stairs,
            warnings=warnings,
            legs=legs,
            polyline=polyline,
            transport_preference=transport_preference,
            uses_shuttle=bool(shuttle_seconds),
            shuttle_fare_yuan=shuttle_fare_yuan if shuttle_seconds else None,
            estimated_wait_minutes=5 if shuttle_seconds else 0,
        )

    def _travel_legs(
        self,
        origin: MapLocation,
        origin_name: str,
        destination: MapLocation,
        destination_name: str,
        guide: MapGuideResponse,
        allow_shuttle: bool,
        *,
        prefer_shuttle: bool = False,
        compare_shuttle_by_time: bool = False,
    ) -> list[RouteLeg]:
        direct = self._walking_leg(origin, origin_name, destination, destination_name)
        shuttle = guide.shuttle
        if not allow_shuttle or shuttle is None or len(shuttle.stations) < 2:
            return [direct]

        boarding = sorted(shuttle.stations, key=lambda station: _distance(origin, station))[:2]
        alighting = sorted(shuttle.stations, key=lambda station: _distance(destination, station))[:2]
        candidates: list[list[RouteLeg]] = []
        for start in boarding:
            for end in alighting:
                if start.id == end.id:
                    continue
                before = self._walking_leg(origin, origin_name, start, start.name)
                after = self._walking_leg(end, end.name, destination, destination_name)
                ride = _shuttle_leg(shuttle, start.id, end.id)
                candidate = [leg for leg in (before, ride, after) if leg.distance_meters or leg.mode == "shuttle"]
                walk_distance = sum(leg.distance_meters for leg in candidate if leg.mode == "walking")
                if (
                    not prefer_shuttle
                    and not compare_shuttle_by_time
                    and walk_distance + 200 >= direct.distance_meters
                ):
                    continue
                candidates.append(candidate)
        if not candidates:
            return [direct]
        best = min(candidates, key=lambda legs: sum(leg.duration_seconds for leg in legs))
        if (
            not prefer_shuttle
            and sum(leg.duration_seconds for leg in best) >= direct.duration_seconds
        ):
            return [direct]
        return best

    def _walking_leg(
        self,
        origin: MapLocation,
        origin_name: str,
        destination: MapLocation,
        destination_name: str,
    ) -> RouteLeg:
        try:
            if _same_location(origin, destination):
                return RouteLeg(
                    from_name=origin_name,
                    to_name=destination_name,
                    distance_meters=0,
                    duration_seconds=0,
                    steps=[],
                    polyline=[origin, destination],
                )
            route = self.amap.walking_route(origin, destination)
        except AmapServiceError as exc:
            raise RoutePlanningError(f"无法规划到「{destination_name}」的步行路线") from exc
        return RouteLeg(
            from_name=origin_name,
            to_name=destination_name,
            distance_meters=route.distance_meters,
            duration_seconds=route.duration_seconds,
            steps=list(route.steps),
            polyline=list(route.polyline),
        )


def _shuttle_leg(
    shuttle: ShuttleService,
    start_id: str,
    end_id: str,
) -> RouteLeg:
    stations = shuttle.stations
    start_index = next(index for index, station in enumerate(stations) if station.id == start_id)
    path = [stations[start_index]]
    index = start_index
    while path[-1].id != end_id:
        index = (index + 1) % len(stations)
        path.append(stations[index])
    polyline = _shuttle_polyline(shuttle, path[0], path[-1])
    distance = round(
        sum(_distance(polyline[index], polyline[index + 1]) for index in range(len(polyline) - 1))
    )
    ride_seconds = math.ceil(distance / (12_000 / 3_600))
    return RouteLeg(
        from_name=path[0].name,
        to_name=path[-1].name,
        distance_meters=distance,
        duration_seconds=ride_seconds + 5 * 60,
        steps=[],
        polyline=polyline,
        mode="shuttle",
        estimated=True,
    )


def _shuttle_polyline(
    shuttle: ShuttleService,
    start: ShuttleStation,
    end: ShuttleStation,
) -> list[MapLocation]:
    points = shuttle.polyline
    start_index = min(range(len(points)), key=lambda index: _distance(points[index], start))
    end_index = min(range(len(points)), key=lambda index: _distance(points[index], end))
    if end_index > start_index:
        return points[start_index : end_index + 1]
    return [*points[start_index:], *points[1 : end_index + 1]]


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
    chosen_sites = {point.site for point in route}
    remaining = [point for point in candidates if point.site not in chosen_sites]
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


def _cover_required_groups(
    origin: MapLocation,
    chosen: list[MapPoint],
    groups: list[list[str]],
    point_by_site: dict[str, MapPoint],
) -> list[MapPoint]:
    """Choose one low-cost venue per animal, sharing venues where possible."""

    route = list(chosen)
    covered = {point.site for point in route}
    remaining = [set(group) for group in groups if not covered.intersection(group)]
    while remaining:
        current_distance = _estimated_distance(origin, route)
        candidates: list[tuple[float, int, str, list[MapPoint]]] = []
        for site in sorted(set().union(*remaining)):
            point = point_by_site[site]
            proposal = route if site in covered else _best_insertion(origin, route, point)
            added_distance = max(
                1, _estimated_distance(origin, proposal) - current_distance
            )
            coverage = sum(site in group for group in remaining)
            candidates.append((added_distance / coverage, -coverage, site, proposal))
        _, _, selected_site, route = min(candidates)
        covered.add(selected_site)
        remaining = [group for group in remaining if selected_site not in group]
    return _two_opt(origin, route)


def _group_cover_sites(
    route: list[MapPoint], groups: list[list[str]]
) -> set[str]:
    """Return route venues whose removal would lose an animal selection."""

    protected: set[str] = set()
    for group in groups:
        if match := next((point.site for point in route if point.site in group), None):
            protected.add(match)
    return protected


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
