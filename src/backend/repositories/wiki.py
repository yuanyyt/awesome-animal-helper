"""Read generated animal Wiki Markdown through its compact manifest."""

from __future__ import annotations

import json
from collections import defaultdict
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
        filtered = [
            item
            for item in self._items
            if (not selected_site or item["site"] == selected_site)
            and (not query or _matches(item, query))
        ]
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


def _matches(item: dict, query: str) -> bool:
    facts = " ".join(str(fact.get("text", "")) for fact in item.get("facts", []))
    text = " ".join(
        [
            str(item.get("site", "")),
            str(item.get("scientific_name", "")),
            str(item.get("animal_name", "")),
            " ".join(str(alias) for alias in item.get("aliases", [])),
            facts,
        ]
    )
    return query in text.casefold()
