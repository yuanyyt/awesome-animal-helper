"""Read structured animal facts from Wikidata entities."""

from decimal import Decimal, InvalidOperation

from .models import StructuredFacts
from .wikipedia_client import WikipediaClient, WikimediaError

WIKIDATA_ENDPOINT = "https://www.wikidata.org/w/api.php"
UNIT_LABELS = {
    "Q573": "天",
    "Q23387": "周",
    "Q5151": "月",
    "Q577": "年",
}


class WikidataClient:
    def __init__(self, transport: WikipediaClient):
        self.transport = transport
        self._entity_cache: dict[str, dict] = {}

    def fetch_facts(self, wikidata_id: str) -> StructuredFacts:
        if not wikidata_id:
            return StructuredFacts(wikidata_id="")
        entity = self._get_entity(wikidata_id)
        if not entity:
            raise WikimediaError(f"Wikidata 实体不存在：{wikidata_id}")

        fields: dict[str, str] = {}
        sources: dict[str, list[str]] = {}
        self._prime_related_entities(entity)

        scientific_name = self._string_values(entity, "P225")
        self._set_field(fields, sources, "scientific_name", scientific_name, "P225")

        if scientific_name or self._item_ids(entity, "P105"):
            taxonomy = self._taxonomy_chain(wikidata_id)
            self._set_field(fields, sources, "taxonomy", taxonomy, "P105/P171/P225")

        self._set_item_field(fields, sources, entity, "habitat", (("P2974", ""),))
        self._set_item_field(
            fields,
            sources,
            entity,
            "distribution",
            (("P9714", "分布于"), ("P183", "特有于"), ("P2341", "原产于")),
        )
        self._set_item_field(fields, sources, entity, "diet", (("P1034", ""),))
        self._set_item_field(
            fields,
            sources,
            entity,
            "behavior",
            (("P9566", "活动节律"), ("P3512", "运动方式")),
        )
        self._set_reproduction(fields, sources, entity)
        self._set_item_field(fields, sources, entity, "conservation_status", (("P141", "IUCN"),))
        return StructuredFacts(wikidata_id=wikidata_id, fields=fields, sources=sources)

    def _set_item_field(
        self,
        fields: dict[str, str],
        sources: dict[str, list[str]],
        entity: dict,
        field: str,
        properties: tuple[tuple[str, str], ...],
    ) -> None:
        parts: list[str] = []
        used: list[str] = []
        for property_id, prefix in properties:
            labels = self._item_labels(entity, property_id)
            if labels:
                value = "、".join(labels)
                parts.append(f"{prefix}：{value}" if prefix else value)
                used.append(property_id)
        if parts:
            fields[field] = "；".join(parts)
            sources[field] = used

    def _set_reproduction(
        self,
        fields: dict[str, str],
        sources: dict[str, list[str]],
        entity: dict,
    ) -> None:
        parts: list[str] = []
        used: list[str] = []
        for property_id, label in (("P13318", "繁殖方式"),):
            values = self._item_labels(entity, property_id)
            if values:
                parts.append(f"{label}：{'、'.join(values)}")
                used.append(property_id)
        for property_id, label in (
            ("P3063", "孕期"),
            ("P7725", "每胎数量"),
            ("P7770", "孵化期"),
            ("P7862", "哺乳期"),
            ("P10322", "育儿袋时间"),
            ("P12432", "性成熟年龄"),
        ):
            values = self._quantity_values(entity, property_id)
            if values:
                parts.append(f"{label}：{'、'.join(values)}")
                used.append(property_id)
        if parts:
            fields["reproduction"] = "；".join(parts)
            sources["reproduction"] = used

    def _taxonomy_chain(self, wikidata_id: str, max_depth: int = 2) -> str:
        chain: list[str] = []
        current_id = wikidata_id
        visited: set[str] = set()
        for _ in range(max_depth):
            if current_id in visited:
                break
            visited.add(current_id)
            entity = self._entity_cache.get(current_id, {})
            if not entity:
                break
            name = next(iter(self._string_values(entity, "P225")), "") or self._label(entity)
            rank_ids = self._item_ids(entity, "P105")
            rank = self._label(self._entity_cache.get(rank_ids[0], {})) if rank_ids else ""
            if name:
                chain.append(f"{rank}：{name}" if rank else name)
            if current_id == "Q729":  # Animalia
                break
            parent_ids = self._item_ids(entity, "P171")
            if not parent_ids:
                break
            current_id = parent_ids[0]
        return "；".join(reversed(chain))

    def _get_entity(self, entity_id: str) -> dict:
        if entity_id in self._entity_cache:
            return self._entity_cache[entity_id]
        self._get_entities((entity_id,))
        return self._entity_cache.get(entity_id, {})

    def _get_entities(self, entity_ids: tuple[str, ...]) -> None:
        missing = tuple(dict.fromkeys(entity_id for entity_id in entity_ids if entity_id not in self._entity_cache))
        if not missing:
            return
        for offset in range(0, len(missing), 50):
            batch = missing[offset : offset + 50]
            data = self.transport.request_json(
                WIKIDATA_ENDPOINT,
                {
                    "action": "wbgetentities",
                    "ids": "|".join(batch),
                    "props": "claims|labels|descriptions",
                    "languages": "zh|en",
                    "languagefallback": "1",
                    "formatversion": "2",
                },
            )
            entities = data.get("entities", {})
            for entity_id in batch:
                self._entity_cache[entity_id] = entities.get(entity_id, {})

    def _prime_related_entities(self, entity: dict) -> None:
        property_ids = (
            "P105", "P171", "P2974", "P9714", "P183", "P2341", "P1034",
            "P9566", "P3512", "P13318", "P141",
        )
        related = tuple(
            item_id
            for property_id in property_ids
            for item_id in self._item_ids(entity, property_id)
        )
        self._get_entities(related)

    def _item_labels(self, entity: dict, property_id: str) -> list[str]:
        labels = [self._label(self._entity_cache.get(item_id, {})) for item_id in self._item_ids(entity, property_id)]
        return [label for label in labels if label]

    def _item_ids(self, entity: dict, property_id: str) -> list[str]:
        values: list[str] = []
        for value in self._claim_values(entity, property_id):
            if isinstance(value, dict) and value.get("id"):
                values.append(value["id"])
        return list(dict.fromkeys(values))

    def _string_values(self, entity: dict, property_id: str) -> list[str]:
        return [value for value in self._claim_values(entity, property_id) if isinstance(value, str)]

    def _quantity_values(self, entity: dict, property_id: str) -> list[str]:
        values: list[str] = []
        for value in self._claim_values(entity, property_id):
            if not isinstance(value, dict) or "amount" not in value:
                continue
            amount = _decimal_text(value["amount"])
            lower = _decimal_text(value.get("lowerBound", value["amount"]))
            upper = _decimal_text(value.get("upperBound", value["amount"]))
            if lower != upper:
                midpoint = (Decimal(lower) + Decimal(upper)) / 2
                error = (Decimal(upper) - Decimal(lower)) / 2
                amount = f"{_decimal_text(midpoint)}±{_decimal_text(error)}"
            unit_id = value.get("unit", "").rsplit("/", 1)[-1]
            unit = UNIT_LABELS.get(unit_id, "")
            values.append(f"{amount}{unit}")
        return values

    @staticmethod
    def _claim_values(entity: dict, property_id: str) -> list[object]:
        statements = entity.get("claims", {}).get(property_id, [])
        preferred = [statement for statement in statements if statement.get("rank") == "preferred"]
        selected = preferred or [statement for statement in statements if statement.get("rank") != "deprecated"]
        values: list[object] = []
        for statement in selected:
            snak = statement.get("mainsnak", {})
            if snak.get("snaktype") == "value" and "datavalue" in snak:
                values.append(snak["datavalue"]["value"])
        return values

    @staticmethod
    def _label(entity: dict) -> str:
        labels = entity.get("labels", {}) if entity else {}
        return labels.get("zh", labels.get("en", {})).get("value", "")

    @staticmethod
    def _set_field(
        fields: dict[str, str],
        sources: dict[str, list[str]],
        field: str,
        values: list[str] | str,
        source: str,
    ) -> None:
        value = "、".join(values) if isinstance(values, list) else values
        if value:
            fields[field] = value
            sources[field] = [source]


def _decimal_text(value: object) -> str:
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    return format(number.normalize(), "f")
