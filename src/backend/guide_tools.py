"""Shared, deterministic tools for text and voice zoo guides."""

from __future__ import annotations

import json
import re
from typing import Any

from .amap_client import AmapClient
from .repository import AnimalRepository
from .route_planner import RoutePlanner
from .schemas import GuideMapContext, MapNamedLocation


class ZooGuideTools:
    """Expose local animal facts and AMap-backed route planning."""

    def __init__(self, amap: AmapClient, repository: AnimalRepository) -> None:
        self.amap = amap
        self.repository = repository
        self.planner = RoutePlanner(amap)

    def search_animals_and_venues(self, query: str) -> dict[str, Any]:
        """Search local animal records and return concise facts and zoo venues."""

        result = self.repository.query(q=query)
        animals = []
        for animal in result.items[:8]:
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
            "matched": result.filtered_count,
            "message": "未找到匹配的本地动物资料" if not animals else "资料来自园区本地数据集",
        }

    def plan_zoo_routes(
        self,
        available_minutes: int,
        energy_level: str,
        selected_sites: list[str] | str | None = None,
        must_see_sites: list[str] | str | None = None,
        origin_name: str | None = None,
        origin_longitude: float | None = None,
        origin_latitude: float | None = None,
        weight_kg: float | None = None,
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

        guide = self.amap.build_guide(self.repository.site_summaries())
        origin = None
        if origin_longitude is not None and origin_latitude is not None:
            origin = MapNamedLocation(
                name=origin_name or "地图选定起点",
                longitude=origin_longitude,
                latitude=origin_latitude,
            )
        routes = self.planner.plan(
            guide=guide,
            selected_sites=normalize_site_list(selected_sites),
            must_see_sites=normalize_site_list(must_see_sites),
            available_minutes=available_minutes,
            energy_level=energy_level,
            origin=origin,
            weight_kg=weight_kg,
        )
        return {"routes": [route.model_dump(mode="json") for route in routes]}

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
