"""Deterministic intent and zoo entity resolution for guide turns."""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.backend.domain.models import GuideMapContext
from src.backend.repositories.animals import AnimalRepository

_SITE_ALIASES = {
    "熊猫馆": "大熊猫",
    "南京熊猫馆": "大熊猫",
    "考拉馆": "考拉",
    "澳洲区": "澳洲袋鼠角",
    "袋鼠角": "澳洲袋鼠角",
    "新狼馆": "狼",
    "热带鸟馆": "犀鸟雨林",
    "虎馆": "虎",
    "熊馆": "熊",
    "狼馆": "狼",
    "象馆": "象",
}
_ANIMAL_ALIASES = {"熊猫": "大熊猫"}


@dataclass(frozen=True)
class TurnResolution:
    """Canonical zoo entities derived before an LLM sees the visitor message."""

    animal_names: tuple[str, ...]
    mentioned_sites: tuple[str, ...]
    resolved_sites: tuple[str, ...]
    must_see_sites: tuple[str, ...]
    must_see_site_groups: tuple[tuple[str, tuple[str, ...]], ...]
    unresolved_terms: tuple[str, ...]

    def as_dependencies(self, map_context: GuideMapContext) -> dict[str, object]:
        return {
            "animal_names": list(self.animal_names),
            "resolved_sites": list(self.resolved_sites),
            "must_see_sites": list(self.must_see_sites),
            "must_see_site_groups": [
                {"label": label, "sites": list(sites)}
                for label, sites in self.must_see_site_groups
            ],
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
        mentioned_animals = self._mentions(message, self.animal_lookup)
        selected_animals, unknown_animals = self.resolve_animal_terms(
            map_context.selected_animals
        )
        animal_names = _unique([*selected_animals, *mentioned_animals])
        mentioned_sites = self._mentions(
            message,
            self.site_lookup,
            allow_embedded_single=False,
        )
        map_sites, unresolved = self.resolve_site_terms(map_context.selected_sites)
        groups = tuple(
            (name, tuple(self.animals_by_name[name].sites))
            for name in animal_names
            if self.animals_by_name[name].sites
        )
        resolved_sites = _unique([*map_sites, *mentioned_sites])
        missing_animals = [
            name for name in animal_names if not self.animals_by_name[name].sites
        ]
        return TurnResolution(
            animal_names=tuple(animal_names),
            mentioned_sites=tuple(mentioned_sites),
            resolved_sites=tuple(resolved_sites),
            must_see_sites=tuple(mentioned_sites),
            must_see_site_groups=groups,
            unresolved_terms=tuple(
                _unique([*unresolved, *unknown_animals, *missing_animals])
            ),
        )

    def resolve_animal_terms(self, terms: list[str]) -> tuple[list[str], list[str]]:
        """Resolve exact animal names and aliases supplied by structured UI state."""

        animals: list[str] = []
        unresolved: list[str] = []
        for term in terms:
            cleaned = term.strip()
            if not cleaned:
                continue
            if animal := self.animal_lookup.get(_normalize(cleaned)):
                animals.append(animal)
            else:
                unresolved.append(cleaned)
        return _unique(animals), _unique(unresolved)

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
    def _mentions(
        message: str,
        lookup: dict[str, str],
        *,
        allow_embedded_single: bool = True,
    ) -> list[str]:
        normalized_message = _normalize(message)
        matches = [
            (match.start(), match.end(), key, value)
            for key, value in lookup.items()
            if key and (allow_embedded_single or len(key) > 1 or key == normalized_message)
            for match in re.finditer(re.escape(key), normalized_message)
        ]
        matches.sort(key=lambda item: (-len(item[2]), item[0]))
        accepted: list[tuple[int, int, str]] = []
        for start, end, _, value in matches:
            overlaps = any(
                start < other_end and end > other_start
                for other_start, other_end, _ in accepted
            )
            if overlaps:
                continue
            accepted.append((start, end, value))
        accepted.sort(key=lambda item: item[0])
        return _unique(value for _, _, value in accepted)


def _normalize(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", value).casefold()


def _unique(values) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
