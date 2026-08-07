"""Command-line entry point for the animal Wikipedia crawler."""

import argparse
import logging
from pathlib import Path
from urllib.parse import quote

from .exporter import write_csv
from .input_reader import read_animals
from .models import AnimalRecord
from .parser import parse_page
from .wikipedia_client import WikipediaClient

LOGGER = logging.getLogger(__name__)


def crawl(
    input_path: Path,
    output_path: Path,
    timeout: float,
    delay: float,
    languages: tuple[str, ...] = ("zh", "en"),
    proxies: tuple[str, ...] = (),
    user_agents: tuple[str, ...] = (),
) -> None:
    animals = read_animals(input_path)
    client = WikipediaClient(timeout=timeout, delay=delay, proxies=proxies, user_agents=user_agents)
    records: list[AnimalRecord] = []
    try:
        for index, animal in enumerate(animals, start=1):
            LOGGER.info("[%d/%d] 抓取 %s", index, len(animals), animal)
            try:
                title, language, html = client.fetch_animal(animal, languages=languages)
                page_url = f"https://{language}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
                page = parse_page(html, title, page_url, language)
                records.append(AnimalRecord(
                    animal=animal,
                    scientific_name=page.fields.get("scientific_name", ""),
                    taxonomy=page.fields.get("taxonomy", ""),
                    habitat=page.fields.get("habitat", ""),
                    distribution=page.fields.get("distribution", ""),
                    diet=page.fields.get("diet", ""),
                    behavior=page.fields.get("behavior", ""),
                    reproduction=page.fields.get("reproduction", ""),
                    conservation_status=page.fields.get("conservation_status", ""),
                    fun_facts=page.fields.get("fun_facts", ""),
                    source_url=page.url,
                    language=language,
                    status="success" if not page.missing_fields else "partial",
                    error="字段缺失：" + "、".join(page.missing_fields) if page.missing_fields else "",
                ))
            except Exception as exc:  # Keep one bad page from stopping the batch.
                LOGGER.warning("抓取 %s 失败：%s", animal, exc)
                records.append(AnimalRecord(animal=animal, error=str(exc)))
    finally:
        client.close()
    write_csv(records, output_path)
    LOGGER.info("已写入 %d 条记录：%s", len(records), output_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从 Wikipedia 抓取动物资料")
    parser.add_argument("--input", type=Path, default=Path("src/data/animal_sites.xlsx"))
    parser.add_argument("--output", type=Path, default=Path("src/data/animals.csv"))
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--jitter", type=float, default=0.5, help="请求间隔额外随机秒数")
    parser.add_argument("--proxy-file", type=Path, help="代理池文件，每行一个 http(s)://代理地址")
    parser.add_argument("--user-agent-file", type=Path, help="User-Agent 文件，每行一个 User-Agent")
    parser.add_argument("--language", choices=("zh", "en"), default="zh")
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args()
    languages = (args.language, "en" if args.language == "zh" else "zh")
    proxies = WikipediaClient.load_proxies(args.proxy_file)
    user_agents = (
        tuple(line.strip() for line in args.user_agent_file.read_text(encoding="utf-8").splitlines() if line.strip())
        if args.user_agent_file else ()
    )
    crawl(args.input, args.output, args.timeout, args.delay, languages, proxies, user_agents)


if __name__ == "__main__":
    main()
