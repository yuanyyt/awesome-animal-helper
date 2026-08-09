"""Data models used by the crawler."""

from dataclasses import dataclass, field


@dataclass
class AnimalRecord:
    """One CSV row for an animal."""

    animal: str
    scientific_name: str = ""
    taxonomy: str = ""
    habitat: str = ""
    distribution: str = ""
    diet: str = ""
    behavior: str = ""
    reproduction: str = ""
    conservation_status: str = ""
    fun_facts: str = ""
    source_url: str = ""
    language: str = ""
    status: str = "failed"
    error: str = ""

    def as_csv_row(self) -> dict[str, str]:
        return {
            "动物": self.animal,
            "学名": self.scientific_name,
            "分类": self.taxonomy,
            "栖息地": self.habitat,
            "分布": self.distribution,
            "食性": self.diet,
            "行为": self.behavior,
            "繁殖": self.reproduction,
            "保护状态": self.conservation_status,
            "趣味事实": self.fun_facts,
            "来源URL": self.source_url,
            "语言": self.language,
            "状态": self.status,
            "错误信息": self.error,
        }


@dataclass
class ParsedPage:
    """Normalized fields extracted from one Wikipedia page."""

    title: str
    url: str
    language: str
    fields: dict[str, str] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)
    article_context: str = ""


@dataclass
class WikipediaPage:
    """Wikipedia page data returned by MediaWiki APIs."""

    title: str
    url: str
    language: str
    page_id: int
    revision_id: int | None
    wikidata_id: str
    extract: str
    html: str


@dataclass
class StructuredFacts:
    """Deterministic facts read from Wikidata."""

    wikidata_id: str
    fields: dict[str, str] = field(default_factory=dict)
    sources: dict[str, list[str]] = field(default_factory=dict)
