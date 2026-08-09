"""Deterministic parsing and text preparation for Wikipedia pages."""

import re
from collections.abc import Iterable

from bs4 import BeautifulSoup, Tag

from .models import ParsedPage, WikipediaPage

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "habitat": ("栖息地", "棲息地", "生境", "生态", "生態", "Habitat", "Ecology"),
    "distribution": ("分布", "分佈", "地理分布", "分布范围", "分佈範圍", "Distribution", "Range"),
    "diet": ("食性", "饮食", "飲食", "觅食", "覓食", "Diet", "Feeding"),
    "behavior": ("行为", "行為", "习性", "習性", "生态", "生態", "Behaviour", "Behavior", "Ecology"),
    "reproduction": ("繁殖", "生殖", "生命周期", "生活史", "Reproduction", "Breeding"),
    "fun_facts": ("趣味事实", "趣味事實", "趣闻", "趣聞", "Trivia"),
}

INFOBOX_ALIASES: dict[str, tuple[str, ...]] = {
    "scientific_name": ("学名", "學名", "taxon", "binomial name", "scientific name"),
    "conservation_status": ("保护状态", "保護狀態", "保护等级", "保護等級", "status"),
}

TAXONOMY_LABELS = ("界", "门", "門", "纲", "綱", "目", "科", "属", "屬", "种", "種")


def parse_page(page: WikipediaPage, context_limit: int = 20_000) -> ParsedPage:
    soup = BeautifulSoup(page.html, "html.parser")
    _remove_noise(soup)
    fields: dict[str, str] = {}

    infobox = soup.select_one("table.infobox")
    if infobox:
        info = _table_fields(infobox)
        for field, aliases in INFOBOX_ALIASES.items():
            fields[field] = _first_matching(info, aliases)
        fields["taxonomy"] = _taxonomy(info)
        if not fields.get("scientific_name"):
            scientific = infobox.select_one(".binomial, .taxon")
            fields["scientific_name"] = _text(scientific) if scientific else ""

    sections = _section_fields(soup)
    for field, aliases in FIELD_ALIASES.items():
        fields[field] = _section_value(sections, aliases)

    article_context = _article_context(page.extract, sections, context_limit)
    missing = [field for field in expected_fields() if not fields.get(field)]
    return ParsedPage(
        title=page.title,
        url=page.url,
        language=page.language,
        fields=fields,
        missing_fields=missing,
        article_context=article_context,
    )


def expected_fields() -> tuple[str, ...]:
    return (
        "scientific_name",
        "taxonomy",
        "habitat",
        "distribution",
        "diet",
        "behavior",
        "reproduction",
        "conservation_status",
        "fun_facts",
    )


def merge_fields(primary: dict[str, str], secondary: dict[str, str]) -> dict[str, str]:
    """Merge facts while preferring non-empty values from the primary source."""
    merged = {field: primary.get(field, "") or secondary.get(field, "") for field in expected_fields()}
    primary_taxonomy = primary.get("taxonomy", "")
    secondary_taxonomy = secondary.get("taxonomy", "")
    if secondary_taxonomy.count("；") > primary_taxonomy.count("；"):
        merged["taxonomy"] = secondary_taxonomy
    return merged


def _remove_noise(soup: BeautifulSoup) -> None:
    for element in soup.select(
        "script, style, table.navbox, .mw-editsection, sup.reference, .reflist, .hatnote, .metadata"
    ):
        element.decompose()


def _table_fields(table: Tag) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in table.select("tr"):
        label = row.find(["th", "td"], class_=re.compile("label", re.I)) or row.find("th")
        value = row.find("td")
        if label and value:
            result[_normalize_heading(_text(label))] = _text(value)
    return result


def _first_matching(fields: dict[str, str], aliases: Iterable[str]) -> str:
    for alias in aliases:
        normalized = _normalize_heading(alias)
        if fields.get(normalized):
            return fields[normalized]
    return ""


def _taxonomy(fields: dict[str, str]) -> str:
    values = [
        f"{label}：{value}"
        for label, value in fields.items()
        if any(token in label for token in TAXONOMY_LABELS) and value
    ]
    return "；".join(dict.fromkeys(values))


def _section_fields(soup: BeautifulSoup) -> dict[str, str]:
    sections: dict[str, str] = {}
    for heading in soup.select("h2, h3"):
        title = _normalize_heading(_text(heading))
        if not title:
            continue
        anchor = heading.parent if _is_heading_wrapper(heading.parent) else heading
        content: list[str] = []
        for sibling in anchor.next_siblings:
            if isinstance(sibling, Tag) and _contains_heading(sibling):
                break
            if isinstance(sibling, Tag):
                if sibling.name in {"p", "ul", "ol"}:
                    text = _text(sibling)
                    if text:
                        content.append(text)
                else:
                    for item in sibling.select("p, li"):
                        text = _text(item)
                        if text:
                            content.append(text)
        value = " ".join(dict.fromkeys(content))
        if value:
            sections[title] = value
    return sections


def _section_value(sections: dict[str, str], aliases: Iterable[str]) -> str:
    normalized_aliases = tuple(_normalize_heading(alias) for alias in aliases)
    for alias in normalized_aliases:
        if sections.get(alias):
            return sections[alias]
    for title, value in sections.items():
        if any(alias in title or title in alias for alias in normalized_aliases):
            return value
    return ""


def _article_context(extract: str, sections: dict[str, str], limit: int) -> str:
    parts = [_clean(extract)] if extract else []
    for title, content in sections.items():
        parts.append(f"## {title}\n{content}")
    return "\n\n".join(dict.fromkeys(part for part in parts if part))[:limit]


def _is_heading_wrapper(element: Tag | None) -> bool:
    return bool(element and any("mw-heading" in value for value in element.get("class", [])))


def _contains_heading(element: Tag) -> bool:
    return element.name in {"h2", "h3"} or bool(element.select_one("h2, h3"))


def _normalize_heading(value: str) -> str:
    value = re.sub(r"\[\s*编辑\s*\]", "", value, flags=re.I)
    return re.sub(r"[\s：:]+", "", value).strip().lower()


def _text(element: Tag) -> str:
    return _clean(element.get_text(" ", strip=True))


def _clean(value: str) -> str:
    value = re.sub(r"\[\s*\d+\s*\]", "", value)
    return re.sub(r"\s+", " ", value).strip()
