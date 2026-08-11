"""Shared, deterministic tools for text and voice zoo guides."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from agno.run import RunContext

from src.backend.domain.models import AnimalDetail, GuideMapContext, MapNamedLocation
from src.backend.integrations.amap.client import AmapClient
from src.backend.repositories.animals import AnimalRepository
from src.backend.services.guide_intent import GuideTurnResolver
from src.backend.services.route_planner import RoutePlanner, RoutePlanningError
from src.backend.services.zoo_services import shuttle_service
from src.backend.services.zoo_time import zoo_operating_status


class AnimalKnowledgeProvider(Protocol):
    """Replaceable source for future narrated animal knowledge."""

    def find(self, query: str, animal_names: list[str]) -> list[AnimalDetail]: ...


class LocalAnimalKnowledgeProvider:
    """Read trusted animal facts from the local CSV-backed repository."""

    def __init__(self, repository: AnimalRepository) -> None:
        self.repository = repository

    def find(self, query: str, animal_names: list[str]) -> list[AnimalDetail]:
        if animal_names:
            return [
                animal
                for name in animal_names
                for animal in self.repository.query(name=name).items
            ][:8]
        return self.repository.query(q=query).items[:8]


class ZooGuideTools:
    """Expose local animal facts and AMap-backed route planning."""

    def __init__(
        self,
        amap: AmapClient,
        repository: AnimalRepository,
        knowledge: AnimalKnowledgeProvider | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.amap = amap
        self.repository = repository
        self.planner = RoutePlanner(amap)
        self.resolver = GuideTurnResolver(repository)
        self.knowledge = knowledge or LocalAnimalKnowledgeProvider(repository)
        self.now_provider = now_provider

    def search_zoo_facilities_for_agent(
        self,
        run_context: RunContext,
        categories: list[str] | str | None = None,
        near_name: str = "",
    ) -> dict[str, Any]:
        """Find visitor facilities without exposing internal coordinate provenance."""

        guide = self.amap.build_guide(self.repository.site_summaries())
        requested = _facility_categories(categories)
        facilities = [
            item for item in guide.facilities if not requested or item.category in requested
        ]
        dependencies = run_context.dependencies or {}
        context = GuideMapContext.model_validate(dependencies.get("map_context") or {})
        reference = context.origin
        label = reference.name if reference else "园区中心"
        normalized_near = near_name.strip()
        if normalized_near:
            resolved_near, _ = self.resolver.resolve_site_terms([normalized_near])
            point = next(
                (
                    point
                    for point in guide.points
                    if point.site in resolved_near
                    or normalized_near in point.site
                    or normalized_near in point.poi_name
                ),
                None,
            )
            if point is not None:
                reference, label = point, point.site
        elif reference is None and context.selected_sites:
            point = next(
                (point for point in guide.points if point.site == context.selected_sites[0]),
                None,
            )
            if point is not None:
                reference, label = point, point.site
        reference = reference or guide.center
        ranked = sorted(facilities, key=lambda item: _map_distance(reference, item))[:12]
        return {
            "near": label,
            "matched": len(facilities),
            "facilities": [
                {
                    "id": item.id,
                    "name": item.name,
                    "category": item.category,
                    "address": item.address,
                    "longitude": item.longitude,
                    "latitude": item.latitude,
                    "distance_meters": round(_map_distance(reference, item)),
                }
                for item in ranked
            ],
        }

    def get_current_zoo_time(self) -> dict[str, object]:
        """Return current Shanghai time and today's shuttle operating status."""

        guide = self.amap.build_guide(self.repository.site_summaries())
        return zoo_operating_status(guide.shuttle or shuttle_service(), self.now_provider)

    def search_animals_and_venues(
        self,
        query: str,
        animal_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """Search local animal records and return concise facts and zoo venues."""

        names = animal_names or list(
            self.resolver.resolve(query, GuideMapContext()).animal_names
        )
        animals = []
        records = self.knowledge.find(query, names)
        for animal in records:
            animals.append(
                {
                    "name": animal.name,
                    "scientific_name": animal.scientific_name,
                    "taxonomy": animal.taxonomy,
                    "habitat": _excerpt(animal.habitat),
                    "distribution": _excerpt(animal.distribution),
                    "diet": _excerpt(animal.diet),
                    "behavior": _excerpt(animal.behavior),
                    "reproduction": _excerpt(animal.reproduction),
                    "conservation_status": _excerpt(animal.conservation_status),
                    "fun_facts": animal.fun_facts[:3],
                    "sites": animal.sites,
                }
            )
        return {
            "animals": animals,
            "matched": len(records),
            "message": "未找到匹配的本地动物资料" if not animals else "资料来自园区本地数据集",
        }

    def search_animals_for_agent(
        self,
        run_context: RunContext,
        query: str = "",
    ) -> dict[str, Any]:
        """Search using canonical animal names resolved before the Agno run."""

        dependencies = run_context.dependencies or {}
        names = _string_list(dependencies.get("animal_names"))
        return self.search_animals_and_venues(query, names)

    def plan_zoo_routes(
        self,
        available_minutes: int,
        energy_level: str,
        selected_sites: list[str] | str | None = None,
        must_see_sites: list[str] | str | None = None,
        must_see_site_groups: list[dict[str, Any]] | None = None,
        origin_name: str | None = None,
        origin_longitude: float | str | None = None,
        origin_latitude: float | str | None = None,
        weight_kg: float | str | None = None,
        transport_preference: str = "walking",
    ) -> dict[str, Any]:
        """Plan three AMap walking itineraries from visitor constraints.

        Args:
            available_minutes: Total available visit time in minutes.
            energy_level: One of 轻松, 一般, 充沛.
            selected_sites: Candidate venues selected on the map or in chat.
            must_see_sites: Venues that must be included if constraints allow.
            origin_name: Human-readable custom starting point.
            origin_longitude: GCJ-02 longitude for a custom start.
            origin_latitude: GCJ-02 latitude for a custom start.
            weight_kg: Optional body weight for calorie estimation.
            transport_preference: Pure walking or a route that may use the shuttle.
        """

        longitude = _optional_float(origin_longitude)
        latitude = _optional_float(origin_latitude)
        weight = _optional_float(weight_kg)
        selected, unknown_selected = self.resolver.resolve_site_terms(
            normalize_site_list(selected_sites)
        )
        must_see, unknown_must_see = self.resolver.resolve_site_terms(
            normalize_site_list(must_see_sites)
        )
        groups: list[tuple[str, list[str]]] = []
        unknown_groups: list[str] = []
        for label, candidates in _site_groups(must_see_site_groups):
            resolved, unknown = self.resolver.resolve_site_terms(candidates)
            if resolved:
                groups.append((label, resolved))
            else:
                unknown_groups.extend(unknown or [label])
        guide = self.amap.build_guide(self.repository.site_summaries())
        mapped_sites = {point.site for point in guide.points}
        requested = _unique([*selected, *must_see])
        mapped_groups = [
            (label, [site for site in candidates if site in mapped_sites])
            for label, candidates in groups
        ]
        missing_group_labels = [label for label, candidates in mapped_groups if not candidates]
        mapped_groups = [(label, candidates) for label, candidates in mapped_groups if candidates]
        unresolved = _unique(
            [
                *unknown_selected,
                *unknown_must_see,
                *unknown_groups,
                *missing_group_labels,
                *(site for site in requested if site not in mapped_sites),
            ]
        )
        routable = [site for site in requested if site in mapped_sites]
        if (requested or groups) and not routable and not mapped_groups:
            raise RoutePlanningError(
                f"这些场馆还没有可靠地图点位：{'、'.join(unresolved)}"
            )
        origin = None
        if longitude is not None and latitude is not None:
            origin = MapNamedLocation(
                name=origin_name or "地图选定起点",
                longitude=longitude,
                latitude=latitude,
            )
        shuttle_status = self.get_current_zoo_time()
        transport = _transport_preference(transport_preference)
        routes = self.planner.plan(
            guide=guide,
            selected_sites=routable,
            must_see_sites=[site for site in must_see if site in mapped_sites],
            must_see_site_groups=[candidates for _, candidates in mapped_groups],
            available_minutes=available_minutes,
            energy_level=energy_level,
            origin=origin,
            weight_kg=weight,
            transport_preference=transport,
            shuttle_operating=bool(shuttle_status["shuttle_operating"]),
            shuttle_fare_yuan=(
                int(shuttle_status["fare_yuan"])
                if shuttle_status["fare_yuan"] is not None
                else None
            ),
        )
        if unresolved:
            warning = f"未加入路线：{'、'.join(unresolved)}（缺少可靠地图点位）"
            routes = [
                route.model_copy(update={"warnings": [*route.warnings, warning]})
                for route in routes
            ]
        return {
            "routes": [route.model_dump(mode="json") for route in routes],
            "resolved_sites": _unique(
                [*routable, *(site for _, candidates in mapped_groups for site in candidates)]
            ),
            "unresolved_sites": unresolved,
        }

    def plan_zoo_routes_for_agent(
        self,
        run_context: RunContext,
        available_minutes: int,
        energy_level: str,
        selected_sites: list[str] | str | None = None,
        must_see_sites: list[str] | str | None = None,
        origin_name: str | None = None,
        origin_longitude: float | str | None = None,
        origin_latitude: float | str | None = None,
        weight_kg: float | str | None = None,
        transport_preference: str = "walking",
    ) -> dict[str, Any]:
        """Plan with canonical per-turn targets injected by Agno."""

        dependencies = run_context.dependencies or {}
        selected = (
            _string_list(dependencies.get("resolved_sites"))
            if "resolved_sites" in dependencies
            else normalize_site_list(selected_sites)
        )
        must_see = (
            _string_list(dependencies.get("must_see_sites"))
            if "must_see_sites" in dependencies
            else normalize_site_list(must_see_sites)
        )
        groups = _site_groups(dependencies.get("must_see_site_groups"))
        map_context = GuideMapContext.model_validate(
            dependencies.get("map_context") or {}
        )
        if map_context.origin is not None:
            origin_name = origin_name or map_context.origin.name
            origin_longitude = _optional_float(origin_longitude)
            origin_latitude = _optional_float(origin_latitude)
            if origin_longitude is None:
                origin_longitude = map_context.origin.longitude
            if origin_latitude is None:
                origin_latitude = map_context.origin.latitude
        return self.plan_zoo_routes(
            available_minutes=available_minutes,
            energy_level=energy_level,
            selected_sites=selected,
            must_see_sites=must_see,
            must_see_site_groups=[
                {"label": label, "sites": sites} for label, sites in groups
            ],
            origin_name=origin_name,
            origin_longitude=origin_longitude,
            origin_latitude=origin_latitude,
            weight_kg=weight_kg,
            transport_preference=transport_preference,
        )

    def plan_with_context(
        self,
        arguments: dict[str, Any],
        context: GuideMapContext,
    ) -> dict[str, Any]:
        """Merge live map context into voice-model tool arguments."""

        values = dict(arguments)
        if not normalize_site_list(values.get("selected_sites")):
            values["selected_sites"] = context.selected_sites
        if context.selected_animals:
            animals, _ = self.resolver.resolve_animal_terms(context.selected_animals)
            values["must_see_site_groups"] = [
                {
                    "label": name,
                    "sites": self.repository.query(name=name).items[0].sites,
                }
                for name in animals
            ]
        if context.origin is not None:
            values.setdefault("origin_name", context.origin.name)
            values.setdefault("origin_longitude", context.origin.longitude)
            values.setdefault("origin_latitude", context.origin.latitude)
        return self.plan_zoo_routes(**values)


def normalize_site_list(value: list[str] | str | None) -> list[str]:
    """Normalize lists emitted by LLM tool calls."""

    if value is None:
        return []
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    text = value.strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return [item.strip() for item in re.split(r"[,，、；;]", text) if item.strip()]


_FACILITY_ALIASES = {
    "卫生间": "toilet",
    "厕所": "toilet",
    "家庭卫生间": "family_toilet",
    "母婴室": "nursing_room",
    "餐饮": "restaurant",
    "餐厅": "restaurant",
    "咖啡": "coffee",
    "饮水": "drinking_water",
    "直饮水": "drinking_water",
    "商店": "shopping",
    "寄存": "bag_storage",
    "停车场": "parking",
    "游客中心": "visitor_center",
    "售票处": "ticket_office",
    "出入口": "entrance",
    "观光车站": "tour_bus_station",
    "警务室": "police",
    "吸烟区": "smoking_area",
}


def _facility_categories(value: list[str] | str | None) -> set[str]:
    items = normalize_site_list(value)
    return {_FACILITY_ALIASES.get(item, item) for item in items}


def _transport_preference(value: str) -> str:
    normalized = value.strip().casefold()
    if normalized in {"mixed", "可乘观光车", "观光车", "混合"}:
        return "mixed"
    if normalized in {"walking", "纯步行", "步行"}:
        return "walking"
    raise RoutePlanningError("出行方式应为纯步行或可乘观光车")


def _map_distance(first: object, second: object) -> float:
    first_longitude = float(getattr(first, "longitude"))
    first_latitude = float(getattr(first, "latitude"))
    second_longitude = float(getattr(second, "longitude"))
    second_latitude = float(getattr(second, "latitude"))
    latitude = math.radians((first_latitude + second_latitude) / 2)
    x = math.radians(second_longitude - first_longitude) * math.cos(latitude)
    y = math.radians(second_latitude - first_latitude)
    return math.hypot(x, y) * 6_371_000


def _excerpt(value: str | None, limit: int = 360) -> str | None:
    if not value:
        return None
    text = " ".join(value.split())
    return text if len(text) <= limit else f"{text[:limit].rstrip()}…"


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _site_groups(value: object) -> list[tuple[str, list[str]]]:
    if not isinstance(value, list):
        return []
    groups: list[tuple[str, list[str]]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "已选动物").strip()
        sites = _string_list(item.get("sites"))
        if sites:
            groups.append((label, sites))
    return groups


def _optional_float(value: float | str | None) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return float(value)


def _unique(values) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
