"""Deterministic parsing rules for Wikipedia HTML pages."""

import re
from collections.abc import Iterable

from bs4 import BeautifulSoup, Tag

from .models import ParsedPage

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "habitat": ("栖息地", "棲息地", "生境", "Habitat"),
    "distribution": ("分布", "分佈", "Distribution", "分布范围", "分佈範圍"),
    "diet": ("食性", "Diet", "饮食", "飲食"),
    "behavior": ("行为", "行為", "习性", "習性", "Behaviour", "Behavior"),
    "reproduction": ("繁殖", "Reproduction", "生殖"),
    "fun_facts": ("趣味事实", "趣味事實", "趣闻", "趣聞", "Trivia"),
}

INFOBOX_ALIASES: dict[str, tuple[str, ...]] = {
    "scientific_name": ("学名", "學名", "binomial name", "scientific name"),
    "conservation_status": ("保护状态", "保護狀態", "保护等级", "保護等級", "status"),
}

TAXONOMY_LABELS = ("界", "门", "綱", "纲", "目", "科", "属", "屬", "种", "種")


def parse_page(html: str, title: str, url: str, language: str) -> ParsedPage:
    soup = BeautifulSoup(html, "html.parser")
    fields: dict[str, str] = {}
    infobox = soup.select_one("table.infobox")
    if infobox:
        info = _table_fields(infobox)
        for field, aliases in INFOBOX_ALIASES.items():
            fields[field] = _first_matching(info, aliases)
        fields["taxonomy"] = _taxonomy(info)

    sections = _section_fields(soup)
    for field, aliases in FIELD_ALIASES.items():
        fields[field] = _section_value(sections, aliases)

    missing = [field for field in _expected_fields() if not fields.get(field)]
    return ParsedPage(title=title, url=url, language=language, fields=fields, missing_fields=missing)


def _expected_fields() -> tuple[str, ...]:
    return (
        "scientific_name", "taxonomy", "habitat", "distribution", "diet",
        "behavior", "reproduction", "conservation_status", "fun_facts",
    )


def _table_fields(table: Tag) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in table.select("tr"):
        label = row.find(["th", "td"], class_=re.compile("label", re.I)) or row.find("th")
        value = row.find("td")
        if label and value:
            key = _clean(label.get_text(" ", strip=True)).lower()
            result[key] = _clean(value.get_text(" ", strip=True))
    return result


def _first_matching(fields: dict[str, str], aliases: Iterable[str]) -> str:
    for alias in aliases:
        value = fields.get(alias.lower())
        if value:
            return value
    return ""


def _taxonomy(fields: dict[str, str]) -> str:
    values = [value for label, value in fields.items() if any(token in label for token in TAXONOMY_LABELS)]
    return "；".join(dict.fromkeys(values))


def _section_fields(soup: BeautifulSoup) -> dict[str, str]:
    sections: dict[str, str] = {}
    for heading in soup.select("h2, h3"):
        title = _clean(heading.get_text(" ", strip=True)).removesuffix("[编辑]").strip()
        if not title:
            continue
        content: list[str] = []
        for sibling in heading.next_siblings:
            if isinstance(sibling, Tag) and sibling.name in {"h2", "h3"}:
                break
            if isinstance(sibling, Tag):
                text = _clean(sibling.get_text(" ", strip=True))
                if text:
                    content.append(text)
        sections[title.lower()] = " ".join(content)
    return sections


def _section_value(sections: dict[str, str], aliases: Iterable[str]) -> str:
    for alias in aliases:
        value = sections.get(alias.lower())
        if value:
            return value
    return ""


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
