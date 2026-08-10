"""Shared, deterministic tools for text and voice zoo guides."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from agno.run import RunContext

from .amap_client import AmapClient
from .guide_intent import GuideTurnResolver
from .repository import AnimalRepository
from .route_planner import RoutePlanner, RoutePlanningError
from .schemas import AnimalDetail, GuideMapContext, MapNamedLocation


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
    ) -> None:
        self.amap = amap
        self.repository = repository
        self.planner = RoutePlanner(amap)
        self.resolver = GuideTurnResolver(repository)
        self.knowledge = knowledge or LocalAnimalKnowledgeProvider(repository)

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
        origin_name: str | None = None,
        origin_longitude: float | str | None = None,
        origin_latitude: float | str | None = None,
        weight_kg: float | str | None = None,
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
        guide = self.amap.build_guide(self.repository.site_summaries())
        mapped_sites = {point.site for point in guide.points}
        requested = _unique([*selected, *must_see])
        unresolved = _unique(
            [
                *unknown_selected,
                *unknown_must_see,
                *(site for site in requested if site not in mapped_sites),
            ]
        )
        routable = [site for site in requested if site in mapped_sites]
        if requested and not routable:
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
        routes = self.planner.plan(
            guide=guide,
            selected_sites=routable,
            must_see_sites=[site for site in must_see if site in mapped_sites],
            available_minutes=available_minutes,
            energy_level=energy_level,
            origin=origin,
            weight_kg=weight,
        )
        if unresolved:
            warning = f"未加入路线：{'、'.join(unresolved)}（缺少可靠地图点位）"
            routes = [
                route.model_copy(update={"warnings": [*route.warnings, warning]})
                for route in routes
            ]
        return {
            "routes": [route.model_dump(mode="json") for route in routes],
            "resolved_sites": routable,
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
            origin_name=origin_name,
            origin_longitude=origin_longitude,
            origin_latitude=origin_latitude,
            weight_kg=weight_kg,
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


def _excerpt(value: str | None, limit: int = 360) -> str | None:
    if not value:
        return None
    text = " ".join(value.split())
    return text if len(text) <= limit else f"{text[:limit].rstrip()}…"


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _optional_float(value: float | str | None) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return float(value)


def _unique(values) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
