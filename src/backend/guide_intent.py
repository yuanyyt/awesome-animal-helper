"""Deterministic intent and zoo entity resolution for guide turns."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .repository import AnimalRepository
from .schemas import GuideMapContext

GuideIntent = Literal["route", "animal_knowledge", "mixed", "unknown"]

_ROUTE_PATTERN = re.compile(
    r"路线|规划|导航|怎么走|怎么去|带我去|先去|下一站|顺路|逛|游览|"
    r"小时|分钟|体力|轻松|充沛|卡路里|千卡|从.+到"
)
_KNOWLEDGE_PATTERN = re.compile(
    r"介绍|了解|讲讲|科普|知识|学名|分类|栖息地|分布|吃什么|食性|"
    r"习性|行为|繁殖|保护状态|趣味|为什么|寿命|特点"
)

_SITE_ALIASES = {
    "熊猫馆": "大熊猫",
    "南京熊猫馆": "大熊猫",
    "考拉馆": "考拉",
    "澳洲区": "澳洲袋鼠角",
    "袋鼠角": "澳洲袋鼠角",
    "新狼馆": "狼",
    "热带鸟馆": "犀鸟雨林",
}
_ANIMAL_ALIASES = {"熊猫": "大熊猫"}


@dataclass(frozen=True)
class TurnResolution:
    """Canonical facts derived before an LLM sees the visitor message."""

    intent: GuideIntent
    animal_names: tuple[str, ...]
    mentioned_sites: tuple[str, ...]
    resolved_sites: tuple[str, ...]
    must_see_sites: tuple[str, ...]
    unresolved_terms: tuple[str, ...]

    def as_dependencies(self, map_context: GuideMapContext) -> dict[str, object]:
        return {
            "intent": self.intent,
            "animal_names": list(self.animal_names),
            "resolved_sites": list(self.resolved_sites),
            "must_see_sites": list(self.must_see_sites),
            "unresolved_terms": list(self.unresolved_terms),
            "map_context": map_context.model_dump(mode="json"),
        }


class GuideTurnResolver:
    """Classify a turn and resolve animal mentions to canonical venues."""

    def __init__(self, repository: AnimalRepository) -> None:
        self.repository = repository
        self.animals = repository.query().items
        self.animals_by_name = {animal.name: animal for animal in self.animals}
        self.sites = tuple(site.name for site in repository.site_summaries())
        self.site_lookup = {_normalize(site): site for site in self.sites}
        self.site_lookup.update(
            {
                _normalize(alias): site
                for alias, site in _SITE_ALIASES.items()
                if site in self.site_lookup.values()
            }
        )

        self.animal_lookup = {_normalize(animal.name): animal.name for animal in self.animals}
        self.animal_lookup.update(
            {
                _normalize(alias): name
                for alias, name in _ANIMAL_ALIASES.items()
                if name in self.animals_by_name
            }
        )
        for animal in self.animals:
            if animal.scientific_name:
                self.animal_lookup[_normalize(animal.scientific_name)] = animal.name

    def resolve(self, message: str, map_context: GuideMapContext) -> TurnResolution:
        animal_names = self._mentions(message, self.animal_lookup)
        mentioned_sites = self._mentions(message, self.site_lookup)
        animal_sites = _unique(
            site
            for name in animal_names
            for site in self.animals_by_name[name].sites
        )
        map_sites, unresolved = self.resolve_site_terms(map_context.selected_sites)
        explicit_sites = _unique([*mentioned_sites, *animal_sites])
        resolved_sites = _unique([*map_sites, *explicit_sites])
        missing_animals = [
            name for name in animal_names if not self.animals_by_name[name].sites
        ]
        return TurnResolution(
            intent=_classify(message, bool(animal_names)),
            animal_names=tuple(animal_names),
            mentioned_sites=tuple(mentioned_sites),
            resolved_sites=tuple(resolved_sites),
            must_see_sites=tuple(explicit_sites),
            unresolved_terms=tuple(_unique([*unresolved, *missing_animals])),
        )

    def resolve_site_terms(self, terms: list[str]) -> tuple[list[str], list[str]]:
        sites: list[str] = []
        unresolved: list[str] = []
        for term in terms:
            cleaned = term.strip()
            if not cleaned:
                continue
            normalized = _normalize(cleaned)
            if site := self.site_lookup.get(normalized):
                sites.append(site)
                continue
            if animal_name := self.animal_lookup.get(normalized):
                animal_sites = self.animals_by_name[animal_name].sites
                if animal_sites:
                    sites.extend(animal_sites)
                    continue
            unresolved.append(cleaned)
        return _unique(sites), _unique(unresolved)

    @staticmethod
    def _mentions(message: str, lookup: dict[str, str]) -> list[str]:
        normalized_message = _normalize(message)
        matches = [
            (key, value)
            for key, value in lookup.items()
            if key and key in normalized_message
        ]
        matches.sort(key=lambda item: len(item[0]), reverse=True)
        return _unique(value for _, value in matches)


def _classify(message: str, has_animal: bool) -> GuideIntent:
    wants_route = bool(_ROUTE_PATTERN.search(message))
    wants_knowledge = bool(_KNOWLEDGE_PATTERN.search(message))
    if wants_route and wants_knowledge:
        return "mixed"
    if wants_route:
        return "route"
    if wants_knowledge or has_animal:
        return "animal_knowledge"
    return "unknown"


def _normalize(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", value).casefold()


def _unique(values) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
