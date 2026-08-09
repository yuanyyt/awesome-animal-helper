"""Generate square animal illustrations with the Qwen Image 3.0 API."""

from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import os
import random
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

LOGGER = logging.getLogger(__name__)

DEFAULT_ENDPOINT = (
    "https://ws-tws8oqjtpbek3bko.cn-beijing.maas.aliyuncs.com/"
    "api/v1/services/aigc/multimodal-generation/generation"
)
DEFAULT_MODEL = "qwen-image-3.0"
DEFAULT_SIZE = "1024*1024"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

STYLE_PROMPT = """为南京红山森林动物园的儿童导览绘制一张正方形动物图鉴插画。
视觉风格必须统一：温暖象牙白纸张背景，森林深绿色的圆润轮廓线，苔藓绿与叶绿色主体色块，少量琥珀橙和陶土棕作点睛；几何化的扁平矢量造型，边缘干净，色块克制，不使用渐变和厚重阴影。气质像认真绘制的自然观察手册，同时友好、活泼、有童趣。
构图要求：单只动物完整入画并居中，采用自然的站立、停栖或游动姿态，主体占画面约65%，保留清晰留白；准确表现该物种最有辨识度的体型、毛色、斑纹、角、喙或尾部特征，不穿衣、不拟人化。背景只用两到四个简化的栖息地植物或地形轮廓，并加入一个很小的叶片或足迹图形作为系列标记。
画面中不得出现文字、字母、数字、标签、边框、Logo或水印；不要摄影写实、3D渲染、动漫风、复杂场景、霓虹色、过度饱和、模糊边缘、畸形肢体或重复身体部位。"""


@dataclass(frozen=True)
class AnimalSource:
    """Animal facts used to identify the illustration subject."""

    name: str
    scientific_name: str = ""
    taxonomy: str = ""
    habitat: str = ""


@dataclass(frozen=True)
class GeneratedImage:
    """Image URL and request metadata returned by the generation API."""

    url: str
    request_id: str = ""


@dataclass
class ManifestEntry:
    """Persistent progress record for one animal."""

    animal: str
    scientific_name: str
    filename: str
    public_path: str
    status: str
    seed: int
    request_id: str = ""
    generated_at: str = ""
    error: str = ""


class QwenImageError(RuntimeError):
    """Raised when image generation or image persistence fails."""


class QwenImageClient:
    """Small synchronous client for the workspace-specific Qwen Image API."""

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        model: str = DEFAULT_MODEL,
        size: str = DEFAULT_SIZE,
        prompt_extend: bool = True,
        timeout: float = 180.0,
        retries: int = 4,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise ValueError("DASHSCOPE_API_KEY 不能为空")
        _validate_square_size(size)
        self.endpoint = endpoint
        self.model = model
        self.size = size
        self.prompt_extend = prompt_extend
        self.retries = max(0, retries)
        self._sleep = sleep
        self._owns_http = http is None
        self._auth_headers = {"Authorization": f"Bearer {api_key}"}
        self.http = http or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
        )

    def close(self) -> None:
        if self._owns_http:
            self.http.close()

    def __enter__(self) -> QwenImageClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def generate(self, prompt: str, seed: int) -> GeneratedImage:
        payload = {
            "model": self.model,
            "input": {
                "messages": [
                    {"role": "user", "content": [{"text": prompt}]},
                ]
            },
            "parameters": {
                "prompt_extend": self.prompt_extend,
                "n": 1,
                "size": self.size,
                "seed": seed,
                "watermark": False,
                "negative_prompt": (
                    "文字，字母，数字，标签，边框，Logo，水印，摄影，3D，动漫，"
                    "拟人服装，复杂背景，渐变，厚重阴影，畸形，多余肢体，模糊"
                ),
            },
        }
        response = self._request(
            "POST",
            self.endpoint,
            json=payload,
            headers=self._auth_headers,
        )
        try:
            data = response.json()
            choices = data["output"]["choices"]
            content = choices[0]["message"]["content"]
            image_url = next(item["image"] for item in content if item.get("image"))
        except (ValueError, KeyError, IndexError, StopIteration, TypeError) as exc:
            raise QwenImageError(f"生成接口未返回有效图片地址：{response.text[:500]}") from exc
        return GeneratedImage(url=image_url, request_id=str(data.get("request_id", "")))

    def download(self, url: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        response = self._request("GET", url)
        image = response.content
        if not image.startswith(PNG_SIGNATURE):
            content_type = response.headers.get("content-type", "unknown")
            raise QwenImageError(f"下载结果不是 PNG 图片（Content-Type: {content_type}）")
        temporary = destination.with_suffix(destination.suffix + ".part")
        temporary.write_bytes(image)
        temporary.replace(destination)

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.http.request(method, url, **kwargs)
                if response.status_code == 429 or response.status_code >= 500:
                    wait = _retry_delay(response, attempt)
                    raise _RetryableError(
                        f"HTTP {response.status_code}",
                        wait,
                    )
                response.raise_for_status()
                return response
            except _RetryableError as exc:
                last_error = exc
                wait = exc.wait
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                wait = min(2**attempt + random.random(), 30.0)
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text[:500]
                raise QwenImageError(
                    f"请求失败（HTTP {exc.response.status_code}）：{detail}"
                ) from exc
            if attempt < self.retries:
                LOGGER.warning("请求暂时失败，%.1f 秒后重试（%d/%d）", wait, attempt + 1, self.retries)
                self._sleep(wait)
        raise QwenImageError(f"请求重试后仍失败：{last_error}") from last_error


class _RetryableError(RuntimeError):
    def __init__(self, message: str, wait: float) -> None:
        super().__init__(message)
        self.wait = wait


def read_animals(path: Path) -> list[AnimalSource]:
    """Read unique animals from the crawler output CSV."""
    animals: list[AnimalSource] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            name = row.get("动物", "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            animals.append(
                AnimalSource(
                    name=name,
                    scientific_name=row.get("学名", "").strip(),
                    taxonomy=row.get("分类", "").strip(),
                    habitat=row.get("栖息地", "").strip(),
                )
            )
    return animals


def build_prompt(animal: AnimalSource) -> str:
    """Combine the shared art direction with species-identifying facts."""
    facts = [f"动物名称：{animal.name}"]
    if animal.scientific_name:
        facts.append(f"拉丁学名：{_compact(animal.scientific_name, 120)}")
    if animal.taxonomy:
        facts.append(f"分类线索：{_compact(animal.taxonomy, 180)}")
    if animal.habitat:
        facts.append(f"栖息地线索：{_compact(animal.habitat, 220)}")
    return f"{STYLE_PROMPT}\n\n本张插画的物种资料：\n" + "\n".join(facts)


def image_filename(animal: AnimalSource) -> str:
    """Build a stable unique ASCII filename, preferring the scientific name."""
    base = animal.scientific_name or animal.name
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    name_hash = hashlib.sha256(animal.name.encode()).hexdigest()[:8]
    if not slug:
        slug = "animal"
    return f"{slug}-{name_hash}.png"


def stable_seed(animal: AnimalSource, base_seed: int) -> int:
    digest = hashlib.sha256(animal.name.encode()).digest()
    offset = int.from_bytes(digest[:4], "big")
    return (base_seed + offset) % 2_147_483_648


def generate_all(
    animals: Iterable[AnimalSource],
    *,
    output_dir: Path,
    manifest_path: Path,
    client: QwenImageClient | None,
    base_seed: int = 20260810,
    overwrite: bool = False,
    dry_run: bool = False,
    fail_fast: bool = False,
    delay: float = 2.0,
    jitter: float = 1.0,
) -> dict[str, ManifestEntry]:
    """Generate every selected animal and persist progress after each item."""
    selected = list(animals)
    entries = read_manifest(manifest_path)
    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    for index, animal in enumerate(selected, start=1):
        filename = image_filename(animal)
        destination = output_dir / filename
        seed = stable_seed(animal, base_seed)
        public_path = f"/animals/{filename}"
        LOGGER.info("[%d/%d] %s -> %s", index, len(selected), animal.name, filename)

        if dry_run:
            entry = ManifestEntry(
                animal.name,
                animal.scientific_name,
                filename,
                public_path,
                "dry-run",
                seed,
            )
            LOGGER.info("提示词预览：\n%s", build_prompt(animal))
        elif destination.exists() and not overwrite:
            previous = entries.get(animal.name)
            entry = ManifestEntry(
                animal.name,
                animal.scientific_name,
                filename,
                public_path,
                "cached",
                seed,
                request_id=previous.request_id if previous else "",
                generated_at=previous.generated_at if previous else "",
            )
        else:
            if client is None:
                raise ValueError("非 dry-run 模式必须提供 QwenImageClient")
            try:
                generated = client.generate(build_prompt(animal), seed)
                client.download(generated.url, destination)
                entry = ManifestEntry(
                    animal.name,
                    animal.scientific_name,
                    filename,
                    public_path,
                    "success",
                    seed,
                    request_id=generated.request_id,
                    generated_at=_now(),
                )
            except Exception as exc:
                LOGGER.error("%s 生成失败：%s", animal.name, exc)
                entry = ManifestEntry(
                    animal.name,
                    animal.scientific_name,
                    filename,
                    public_path,
                    "failed",
                    seed,
                    generated_at=_now(),
                    error=str(exc),
                )
                if fail_fast:
                    entries[animal.name] = entry
                    write_manifest(entries.values(), manifest_path)
                    raise

        entries[animal.name] = entry
        if not dry_run:
            write_manifest(entries.values(), manifest_path)
        if entry.status == "success" and index < len(selected):
            time.sleep(max(0.0, delay) + random.uniform(0, max(0.0, jitter)))

    return entries


def read_manifest(path: Path) -> dict[str, ManifestEntry]:
    if not path.exists():
        return {}
    entries: dict[str, ManifestEntry] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                entry = ManifestEntry(
                    animal=row["animal"],
                    scientific_name=row["scientific_name"],
                    filename=row["filename"],
                    public_path=row["public_path"],
                    status=row["status"],
                    seed=int(row["seed"]),
                    request_id=row.get("request_id", ""),
                    generated_at=row.get("generated_at", ""),
                    error=row.get("error", ""),
                )
            except (KeyError, ValueError) as exc:
                raise ValueError(f"图片清单格式错误：{path}") from exc
            entries[entry.animal] = entry
    return entries


def write_manifest(entries: Iterable[ManifestEntry], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(ManifestEntry.__dataclass_fields__)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(asdict(entry) for entry in entries)
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="使用 Qwen Image 3.0 批量生成动物方形插画")
    parser.add_argument("--input", type=Path, default=Path("src/data/animals.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("src/frontend/public/animals"))
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("src/frontend/public/animals/manifest.csv"),
    )
    parser.add_argument("--animal", action="append", default=[], help="只生成指定动物，可重复传入")
    parser.add_argument("--limit", type=int, help="只处理前 N 个动物，便于小批量验证")
    parser.add_argument("--model", default=os.getenv("QWEN_IMAGE_MODEL", DEFAULT_MODEL))
    parser.add_argument("--endpoint", default=os.getenv("QWEN_IMAGE_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--size", default=DEFAULT_SIZE, help="正方形分辨率，例如 1024*1024")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--delay", type=float, default=2.0, help="成功请求后的基础间隔秒数")
    parser.add_argument("--jitter", type=float, default=1.0, help="额外随机间隔秒数")
    parser.add_argument("--base-seed", type=int, default=20260810)
    parser.add_argument("--prompt-extend", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--overwrite", action="store_true", help="重新生成已存在的图片")
    parser.add_argument("--dry-run", action="store_true", help="仅打印提示词，不调用 API 或写文件")
    parser.add_argument("--fail-fast", action="store_true", help="任一动物失败后立即退出")
    return parser


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args()
    _validate_args(args)

    animals = read_animals(args.input)
    if args.animal:
        wanted = set(args.animal)
        animals = [animal for animal in animals if animal.name in wanted]
        missing = wanted - {animal.name for animal in animals}
        if missing:
            raise SystemExit(f"CSV 中找不到动物：{'、'.join(sorted(missing))}")
    if args.limit is not None:
        animals = animals[: args.limit]
    if not animals:
        raise SystemExit("没有可生成的动物")

    if args.dry_run:
        generate_all(
            animals,
            output_dir=args.output_dir,
            manifest_path=args.manifest,
            client=None,
            base_seed=args.base_seed,
            overwrite=args.overwrite,
            dry_run=True,
            fail_fast=args.fail_fast,
            delay=args.delay,
            jitter=args.jitter,
        )
        return

    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    if not api_key:
        raise SystemExit("缺少 DASHSCOPE_API_KEY，请在 .env 中配置")
    with QwenImageClient(
        api_key,
        endpoint=args.endpoint,
        model=args.model,
        size=args.size,
        prompt_extend=args.prompt_extend,
        timeout=args.timeout,
        retries=args.retries,
    ) as client:
        entries = generate_all(
            animals,
            output_dir=args.output_dir,
            manifest_path=args.manifest,
            client=client,
            base_seed=args.base_seed,
            overwrite=args.overwrite,
            fail_fast=args.fail_fast,
            delay=args.delay,
            jitter=args.jitter,
        )
    failed = [animal.name for animal in animals if entries[animal.name].status == "failed"]
    LOGGER.info("处理完成：%d 个动物，失败 %d 个", len(animals), len(failed))
    if failed:
        raise SystemExit(1)


def _validate_args(args: argparse.Namespace) -> None:
    _validate_square_size(args.size)
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit 必须大于 0")
    if not 0 <= args.base_seed <= 2_147_483_647:
        raise SystemExit("--base-seed 必须在 0 到 2147483647 之间")


def _validate_square_size(size: str) -> None:
    match = re.fullmatch(r"(\d+)\*(\d+)", size)
    if not match or match.group(1) != match.group(2):
        raise ValueError("--size 必须是 1:1 分辨率，例如 1024*1024")
    side = int(match.group(1))
    if not 512 <= side <= 2048:
        raise ValueError("图片边长必须在 512 到 2048 像素之间")


def _compact(value: str, limit: int) -> str:
    compacted = re.sub(r"\s+", " ", value).strip()
    return compacted if len(compacted) <= limit else compacted[: limit - 1] + "…"


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("retry-after", "").strip()
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), 60.0)
        except ValueError:
            pass
    return min(2**attempt + random.random(), 30.0)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


if __name__ == "__main__":
    main()
