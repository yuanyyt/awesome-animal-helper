"""Read generated animal Wiki Markdown through its compact manifest."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from src.backend.domain.models import (
    WikiAnimalSummary,
    WikiFact,
    WikiIndexResponse,
    WikiPageResponse,
    WikiScientificGroup,
    WikiSiteGroup,
)

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "wx_info" / "wiki"
MANIFEST_PATH = DATA_DIR / "manifest.json"
_SEARCH_TOKEN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")


class WikiRepository:
    """Reload-on-change view of the generated Wiki manifest."""

    def __init__(self, manifest_path: Path = MANIFEST_PATH) -> None:
        self.manifest_path = manifest_path
        self.wiki_root = manifest_path.parent.resolve()
        self._mtime_ns = -1
        self._generated_at = ""
        self._items: list[dict] = []

    def query(self, *, q: str | None = None, site: str | None = None) -> WikiIndexResponse:
        self._ensure_current()
        query = (q or "").strip().casefold()
        selected_site = (site or "").strip()
        candidates = [
            item for item in self._items if not selected_site or item["site"] == selected_site
        ]
        if query:
            ranked = [(_match_score(item, query), index, item) for index, item in enumerate(candidates)]
            filtered = [
                item
                for score, _, item in sorted(ranked, key=lambda row: (-row[0], row[1]))
                if score > 0
            ]
        else:
            filtered = candidates
        grouped: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        for item in filtered:
            grouped[item["site"]][item["scientific_name"]].append(item)
        sites = [
            WikiSiteGroup(
                name=site_name,
                fact_count=sum(item["fact_count"] for animals in groups.values() for item in animals),
                scientific_groups=[
                    WikiScientificGroup(
                        scientific_name=scientific_name,
                        animals=[_summary(item) for item in animals],
                    )
                    for scientific_name, animals in groups.items()
                ],
            )
            for site_name, groups in grouped.items()
        ]
        return WikiIndexResponse(
            sites=sites,
            total_animals=len(self._items),
            total_facts=sum(item["fact_count"] for item in self._items),
            filtered_count=len(filtered),
            generated_at=self._generated_at,
        )

    def get_page(self, *, site: str, scientific_name: str, animal: str) -> WikiPageResponse:
        self._ensure_current()
        item = next(
            (
                candidate
                for candidate in self._items
                if candidate["site"] == site
                and candidate["scientific_name"] == scientific_name
                and candidate["animal_name"] == animal
            ),
            None,
        )
        if item is None:
            raise KeyError(animal)
        markdown_path = (self.wiki_root / item["page_file"]).resolve()
        if not markdown_path.is_relative_to(self.wiki_root) or not markdown_path.is_file():
            raise KeyError(animal)
        return WikiPageResponse(
            **_summary(item).model_dump(),
            facts=[WikiFact.model_validate(fact) for fact in item.get("facts", [])],
            markdown=markdown_path.read_text(encoding="utf-8"),
        )

    def fact_counts(self) -> dict[str, int]:
        self._ensure_current()
        counts: dict[str, int] = defaultdict(int)
        for item in self._items:
            counts[item["animal_name"]] += int(item.get("fact_count", 0))
        return dict(counts)

    def search_facts(
        self,
        query: str,
        animal_names: Sequence[str] = (),
        site_names: Sequence[str] = (),
        limit: int = 6,
    ) -> list[dict]:
        """Return compact, ranked Wiki facts without loading page Markdown."""

        self._ensure_current()
        query_text = _normalize(query)
        selected_animals = {_normalize(name) for name in animal_names if name}
        preferred_sites = {_normalize(name) for name in site_names if name}
        query_tokens = _search_tokens(query_text)
        ranked: list[tuple[int, int, int, dict]] = []
        for item_index, item in enumerate(self._items):
            animal = str(item.get("animal_name", ""))
            aliases = [str(alias) for alias in item.get("aliases", [])]
            identities = {_normalize(animal), *(_normalize(alias) for alias in aliases)}
            if selected_animals and identities.isdisjoint(selected_animals):
                continue

            site = str(item.get("site", ""))
            scientific_name = str(item.get("scientific_name", ""))
            identity_score = 100 if selected_animals else 0
            identity_score += 30 * sum(
                bool(value and value in query_text)
                for value in [*identities, _normalize(scientific_name)]
            )
            if _normalize(site) in preferred_sites:
                identity_score += 20
            if site and len(_normalize(site)) > 1 and _normalize(site) in query_text:
                identity_score += 20

            for fact_index, fact in enumerate(item.get("facts", [])):
                if not isinstance(fact, dict):
                    continue
                fact_text = str(fact.get("text", ""))
                evidence = str(fact.get("evidence", ""))
                searchable = _normalize(f"{fact_text} {evidence}")
                content_score = sum(token in searchable for token in query_tokens)
                source = fact.get("source", {})
                source = source if isinstance(source, dict) else {}
                source_title = _normalize(str(source.get("title", "")))
                title_score = 8 * sum(token in source_title for token in query_tokens)
                score = identity_score + content_score + title_score
                if score <= 0:
                    continue
                ranked.append(
                    (
                        score,
                        item_index,
                        fact_index,
                        {
                            "animal_name": animal,
                            "scientific_name": scientific_name,
                            "site": site,
                            "text": fact_text,
                            "evidence": evidence,
                            "source": source,
                        },
                    )
                )
        ranked.sort(key=lambda row: (-row[0], row[1], row[2]))
        return [row[3] for row in ranked[: max(1, min(limit, 6))]]

    def _ensure_current(self) -> None:
        try:
            mtime_ns = self.manifest_path.stat().st_mtime_ns
        except FileNotFoundError:
            self._mtime_ns = -1
            self._generated_at = ""
            self._items = []
            return
        if mtime_ns == self._mtime_ns:
            return
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        items = data.get("items", [])
        if not isinstance(items, list):
            raise ValueError("Wiki manifest.items 必须是数组")
        self._items = [item for item in items if isinstance(item, dict)]
        self._generated_at = str(data.get("generated_at", ""))
        self._mtime_ns = mtime_ns


def _summary(item: dict) -> WikiAnimalSummary:
    return WikiAnimalSummary(
        site=str(item["site"]),
        scientific_name=str(item["scientific_name"]),
        animal_name=str(item["animal_name"]),
        aliases=[str(alias) for alias in item.get("aliases", [])],
        fact_count=int(item.get("fact_count", 0)),
        source_count=int(item.get("source_count", 0)),
    )


def _match_score(item: dict, query: str) -> int:
    identity = " ".join(
        str(value)
        for value in [
            item.get("site", ""),
            item.get("scientific_name", ""),
            item.get("animal_name", ""),
            *(item.get("aliases", []) if isinstance(item.get("aliases"), list) else []),
        ]
    )
    fact_text = " ".join(
        f"{fact.get('text', '')} {fact.get('evidence', '')}"
        for fact in item.get("facts", [])
        if isinstance(fact, dict)
    )
    source_titles = " ".join(
        str(fact.get("source", {}).get("title", ""))
        for fact in item.get("facts", [])
        if isinstance(fact, dict) and isinstance(fact.get("source"), dict)
    )
    normalized_query = _normalize(query)
    searchable = _normalize(f"{identity} {fact_text} {source_titles}")
    if normalized_query in searchable:
        return 1000
    tokens = _search_tokens(normalized_query)
    required_matches = min(2, len(tokens))
    matched_tokens = {token for token in tokens if token in searchable}
    if not required_matches or len(matched_tokens) < required_matches:
        return 0
    normalized_identity = _normalize(identity)
    normalized_facts = _normalize(fact_text)
    normalized_titles = _normalize(source_titles)
    return sum(
        8 * (token in normalized_titles)
        + 4 * (token in normalized_identity)
        + (token in normalized_facts)
        for token in matched_tokens
    )


def _normalize(value: str) -> str:
    return "".join(value.casefold().split())


def _search_tokens(query: str) -> set[str]:
    parts = _SEARCH_TOKEN.findall(query)
    latin = {part for part in parts if len(part) >= 2 and part.isascii()}
    chinese = "".join(part for part in parts if not part.isascii())
    return latin | {chinese[index : index + 2] for index in range(len(chinese) - 1)}
