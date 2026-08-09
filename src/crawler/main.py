"""Command-line entry point for the animal knowledge crawler."""

import argparse
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from .exporter import write_csv
from .input_reader import read_animals
from .llm_extractor import LlmExtractionError, LlmExtractor
from .models import AnimalRecord, StructuredFacts
from .parser import expected_fields, merge_fields, parse_page
from .wikidata_client import WikidataClient
from .wikipedia_client import WikipediaClient

LOGGER = logging.getLogger(__name__)


def crawl(
    input_path: Path,
    output_path: Path,
    timeout: float,
    delay: float,
    jitter: float = 0.5,
    languages: tuple[str, ...] = ("zh", "en"),
    proxies: tuple[str, ...] = (),
    user_agents: tuple[str, ...] = (),
    llm: LlmExtractor | None = None,
) -> None:
    animals = read_animals(input_path)
    wikipedia = WikipediaClient(
        timeout=timeout,
        delay=delay,
        jitter=jitter,
        proxies=proxies,
        user_agents=user_agents,
    )
    wikidata = WikidataClient(wikipedia)
    records: list[AnimalRecord] = []
    try:
        for index, animal in enumerate(animals, start=1):
            LOGGER.info("[%d/%d] 抓取 %s", index, len(animals), animal)
            records.append(_crawl_animal(animal, wikipedia, wikidata, languages, llm))
            write_csv(records, output_path)
    finally:
        wikipedia.close()
    write_csv(records, output_path)
    LOGGER.info("已写入 %d 条记录：%s", len(records), output_path)


def _crawl_animal(
    animal: str,
    wikipedia: WikipediaClient,
    wikidata: WikidataClient,
    languages: tuple[str, ...],
    llm: LlmExtractor | None,
) -> AnimalRecord:
    try:
        page = wikipedia.fetch_animal(animal, languages=languages)
    except Exception as exc:
        LOGGER.warning("抓取 %s 失败：%s", animal, exc)
        return AnimalRecord(animal=animal, error=str(exc))

    parsed = parse_page(page)
    errors: list[str] = []
    facts = StructuredFacts(wikidata_id=page.wikidata_id)
    if page.wikidata_id:
        try:
            facts = wikidata.fetch_facts(page.wikidata_id)
        except Exception as exc:
            errors.append(f"Wikidata：{exc}")
            LOGGER.warning("%s 的 Wikidata 解析失败：%s", animal, exc)

    fields = merge_fields(facts.fields, parsed.fields)
    if llm is not None:
        try:
            llm_result = llm.clean(
                animal=animal,
                structured_fields=facts.fields,
                wikipedia_fields=parsed.fields,
                article_context=parsed.article_context,
                structured_sources=facts.sources,
            )
            fields = llm_result.fields
            LOGGER.info(
                "%s LLM 置信度 %.2f%s",
                animal,
                llm_result.confidence,
                f"；警告：{'；'.join(llm_result.warnings)}" if llm_result.warnings else "",
            )
            LOGGER.debug("%s LLM 字段证据：%s", animal, llm_result.evidence)
        except LlmExtractionError as exc:
            errors.append(str(exc))
            LOGGER.warning("%s 的 LLM 清洗失败，保留确定性结果：%s", animal, exc)

    missing = [field for field in expected_fields() if not fields.get(field)]
    if missing:
        errors.append("字段缺失：" + "、".join(_FIELD_LABELS[field] for field in missing))
    return _record_from_fields(
        animal=animal,
        fields=fields,
        source_url=page.url,
        language=page.language,
        status="success" if not missing and not errors else "partial",
        error="；".join(errors),
    )


def _record_from_fields(
    animal: str,
    fields: dict[str, str],
    source_url: str,
    language: str,
    status: str,
    error: str,
) -> AnimalRecord:
    return AnimalRecord(
        animal=animal,
        scientific_name=fields.get("scientific_name", ""),
        taxonomy=fields.get("taxonomy", ""),
        habitat=fields.get("habitat", ""),
        distribution=fields.get("distribution", ""),
        diet=fields.get("diet", ""),
        behavior=fields.get("behavior", ""),
        reproduction=fields.get("reproduction", ""),
        conservation_status=fields.get("conservation_status", ""),
        fun_facts=fields.get("fun_facts", ""),
        source_url=source_url,
        language=language,
        status=status,
        error=error,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从 Wikipedia、Wikidata 和可选 LLM 提取动物资料")
    parser.add_argument("--input", type=Path, default=Path("src/data/animal_sites.xlsx"))
    parser.add_argument("--output", type=Path, default=Path("src/data/animals.csv"))
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--jitter", type=float, default=0.5, help="请求间隔额外随机秒数")
    parser.add_argument("--proxy-file", type=Path, help="代理池文件，每行一个 http(s)://代理地址")
    parser.add_argument("--user-agent-file", type=Path, help="User-Agent 文件，每行一个 User-Agent")
    parser.add_argument("--language", choices=("zh", "en"), default="zh")
    parser.add_argument("--llm-model", default=os.getenv("LLM_MODEL", ""), help="OpenAI 兼容模型名")
    parser.add_argument("--llm-base-url", default=os.getenv("LLM_BASE_URL"), help="OpenAI 兼容 API 地址")
    parser.add_argument("--llm-timeout", type=float, default=60.0)
    parser.add_argument("--no-llm", action="store_true", help="禁用 LLM，仅使用确定性提取")
    return parser


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args()
    languages = (args.language, "en" if args.language == "zh" else "zh")
    proxies = WikipediaClient.load_lines(args.proxy_file)
    user_agents = WikipediaClient.load_lines(args.user_agent_file)
    llm = _build_llm(args)
    crawl(
        input_path=args.input,
        output_path=args.output,
        timeout=args.timeout,
        delay=args.delay,
        jitter=args.jitter,
        languages=languages,
        proxies=proxies,
        user_agents=user_agents,
        llm=llm,
    )


def _build_llm(args: argparse.Namespace) -> LlmExtractor | None:
    if args.no_llm:
        return None
    api_key = os.getenv("LLM_API_KEY", "")
    if not api_key or not args.llm_model:
        LOGGER.warning("未配置 LLM_API_KEY/LLM_MODEL，将仅使用 Wikipedia 与 Wikidata")
        return None
    return LlmExtractor(
        api_key=api_key,
        model=args.llm_model,
        base_url=args.llm_base_url,
        timeout=args.llm_timeout,
    )


_FIELD_LABELS = {
    "scientific_name": "学名",
    "taxonomy": "分类",
    "habitat": "栖息地",
    "distribution": "分布",
    "diet": "食性",
    "behavior": "行为",
    "reproduction": "繁殖",
    "conservation_status": "保护状态",
    "fun_facts": "趣味事实",
}


if __name__ == "__main__":
    main()
