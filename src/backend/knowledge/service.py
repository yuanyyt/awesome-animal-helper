"""Build and query the persistent animal knowledge index."""

from __future__ import annotations

import fcntl
import os
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from src.backend.knowledge.config import KnowledgeConfig
from src.backend.knowledge.database import KnowledgeDatabase, insert_chunks
from src.backend.knowledge.embeddings import Embedder, EmbeddingError, OpenAIEmbedder
from src.backend.repositories.animals import AnimalRepository

MAX_BATCH_SIZE = 10
MAX_CHUNK_CHARS = 900
MAX_CONTEXT_CHARS = 6_000
MIN_VECTOR_SIMILARITY = 0.35
RRF_K = 60

_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？；])")
_HEADING_PUNCTUATION = set("。！？；，：,.!?;")
_ANIMAL_ALIASES = {"熊猫": "大熊猫"}


class KnowledgeBuildError(RuntimeError):
    """Raised when the persistent knowledge database cannot be initialized."""


class KnowledgeService:
    """Own the one-time build and hybrid retrieval lifecycle."""

    def __init__(
        self,
        config: KnowledgeConfig | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self.config = config or KnowledgeConfig.from_env()
        self.database = KnowledgeDatabase(
            self.config.database_path,
            self.config.embedding_dimensions,
        )
        self._embedder = embedder

    @property
    def ready(self) -> bool:
        return self.database.ready()

    def ensure_ready(self) -> bool:
        """Build only when no complete persistent database exists."""

        if self.ready:
            return True
        self.config.database_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.config.database_path.with_suffix(".lock")
        with lock_path.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not self.ready:
                self._build_and_replace()
        return True

    def rebuild(self) -> None:
        """Explicitly replace the current database from checked-in sources."""

        self.config.database_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.config.database_path.with_suffix(".lock")
        with lock_path.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self._build_and_replace()

    def search(
        self,
        query: str,
        animal_names: Sequence[str],
        top_k: int = 6,
    ) -> dict[str, Any]:
        """Combine deterministic animal mappings with semantic chunk retrieval."""

        if not self.ready:
            raise KnowledgeBuildError("动物知识库尚未初始化")
        names = list(dict.fromkeys(name for name in animal_names if name))[:8]
        exact = self.database.exact_chunks(names)
        vector_rows: list[dict[str, Any]] = []
        try:
            vector = self.embedder.embed([query])[0]
            vector_rows = self.database.vector_chunks(vector, names)
        except (EmbeddingError, IndexError):
            vector_rows = []

        chunks = _fuse_chunks(exact, vector_rows, min(max(top_k, 1), 6))
        chunks = _limit_content(chunks, MAX_CONTEXT_CHARS)
        if not names:
            names = self.database.mapped_animals([item["id"] for item in chunks])
        return {
            "animals": self.database.animal_records(names),
            "chunks": [_public_chunk(item, self.database) for item in chunks],
            "matched": len(chunks),
            "semantic_available": bool(vector_rows),
            "message": "未找到匹配的本地动物资料" if not chunks and not names else "资料来自园区本地知识库",
        }

    def neighbors(
        self,
        chunk_ids: Sequence[int],
        before: int = 1,
        after: int = 1,
    ) -> list[dict[str, Any]]:
        """Return ordered neighboring chunks without crossing a section."""

        if not self.ready:
            raise KnowledgeBuildError("动物知识库尚未初始化")
        ids = list(dict.fromkeys(int(value) for value in chunk_ids))[:6]
        rows = self.database.neighboring_chunks(
            ids,
            max(0, min(before, 2)),
            max(0, min(after, 2)),
        )
        return _limit_content(rows, MAX_CONTEXT_CHARS)

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = OpenAIEmbedder(self.config)
        return self._embedder

    def _build_and_replace(self) -> None:
        temporary = self.config.database_path.with_name(
            f".{self.config.database_path.name}.building-{os.getpid()}"
        )
        if temporary.exists():
            temporary.unlink()
        database = KnowledgeDatabase(temporary, self.config.embedding_dimensions)
        try:
            repository = AnimalRepository(
                self.config.animals_path,
                self.config.sites_path,
            )
            animals = repository.query().items
            chunks = parse_intro(
                self.config.intro_path,
                [animal.name for animal in animals],
                [site.name for site in repository.site_summaries()],
            )
            vectors = _embed_chunks(self.embedder, chunks)
            with database.connect() as connection:
                database.create_schema(connection)
                database.insert_animals(
                    connection,
                    animals,
                    [site.name for site in repository.site_summaries()],
                )
                insert_chunks(connection, chunks, vectors)
                connection.executemany(
                    "INSERT INTO metadata(key, value) VALUES (?, ?)",
                    [
                        ("initialized", "1"),
                        ("embedding_model", self.config.embedding_model),
                        ("embedding_dimensions", str(self.config.embedding_dimensions)),
                    ],
                )
            os.replace(temporary, self.config.database_path)
        except Exception as exc:
            if temporary.exists():
                temporary.unlink()
            if isinstance(exc, KnowledgeBuildError):
                raise
            raise KnowledgeBuildError("动物知识库初始化失败") from exc


def parse_intro(
    path: Path,
    animal_names: Sequence[str],
    site_names: Sequence[str],
) -> list[dict[str, Any]]:
    """Split the local narration into indexed, animal-linked chunks."""

    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise KnowledgeBuildError("无法读取 intro.md") from exc
    known_headings = set(animal_names) | set(site_names)
    section = "动物园讲解"
    chunks: list[dict[str, Any]] = []
    index = 0
    for raw_line in lines:
        text = raw_line.strip()
        if not text:
            continue
        if _is_heading(text, known_headings):
            section = text
            continue
        for part in _split_paragraph(text):
            chunks.append(
                {
                    "document_id": "intro.md",
                    "chunk_index": index,
                    "section": section,
                    "content": part,
                    "animal_names": _animal_mentions(part, animal_names),
                }
            )
            index += 1
    if not chunks:
        raise KnowledgeBuildError("intro.md 中没有可索引的讲解内容")
    return chunks


def _embed_chunks(
    embedder: Embedder,
    chunks: Sequence[dict[str, Any]],
) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(chunks), MAX_BATCH_SIZE):
        texts = [item["content"] for item in chunks[start : start + MAX_BATCH_SIZE]]
        vectors.extend(embedder.embed(texts))
    if len(vectors) != len(chunks):
        raise EmbeddingError("讲解段落向量数量不完整")
    return vectors


def _is_heading(text: str, known_headings: set[str]) -> bool:
    if text in known_headings:
        return True
    return len(text) <= 30 and not any(mark in text for mark in _HEADING_PUNCTUATION)


def _split_paragraph(text: str) -> Iterable[str]:
    if len(text) <= MAX_CHUNK_CHARS:
        yield text
        return
    current = ""
    for sentence in filter(None, _SENTENCE_BOUNDARY.split(text)):
        if len(current) + len(sentence) <= MAX_CHUNK_CHARS:
            current += sentence
            continue
        if current:
            yield current
        while len(sentence) > MAX_CHUNK_CHARS:
            yield sentence[:MAX_CHUNK_CHARS]
            sentence = sentence[MAX_CHUNK_CHARS:]
        current = sentence
    if current:
        yield current


def _animal_mentions(text: str, animal_names: Sequence[str]) -> list[str]:
    terms = [(name, name) for name in animal_names]
    terms.extend(
        (alias, canonical)
        for alias, canonical in _ANIMAL_ALIASES.items()
        if canonical in animal_names
    )
    occupied = [False] * len(text)
    found: dict[str, int] = {}
    for term, canonical in sorted(terms, key=lambda item: len(item[0]), reverse=True):
        start = 0
        while (position := text.find(term, start)) >= 0:
            end = position + len(term)
            if not any(occupied[position:end]):
                occupied[position:end] = [True] * len(term)
                found[canonical] = min(found.get(canonical, position), position)
            start = position + len(term)
    return [name for name, _ in sorted(found.items(), key=lambda item: item[1])]


def _fuse_chunks(
    exact: Sequence[dict[str, Any]],
    vectors: Sequence[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    scores: dict[int, float] = {}
    rows: dict[int, dict[str, Any]] = {}
    for rank, row in enumerate(exact, start=1):
        rows[row["id"]] = row
        scores[row["id"]] = scores.get(row["id"], 0.0) + 1 / (RRF_K + rank)
    for rank, row in enumerate(vectors, start=1):
        if row["similarity"] < MIN_VECTOR_SIMILARITY:
            continue
        rows[row["id"]] = row
        scores[row["id"]] = scores.get(row["id"], 0.0) + 1 / (RRF_K + rank)
    ranked = sorted(rows.values(), key=lambda row: (-scores[row["id"]], row["chunk_index"]))
    return ranked[:limit]


def _limit_content(rows: Sequence[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    size = 0
    for row in rows:
        content = row["content"]
        if selected and size + len(content) > limit:
            break
        selected.append(row)
        size += len(content)
    return selected


def _public_chunk(
    row: dict[str, Any],
    database: KnowledgeDatabase,
) -> dict[str, Any]:
    section_rows = database.neighboring_chunks([row["id"]], 1, 1)
    indexes = {item["chunk_index"] for item in section_rows}
    return {
        "id": row["id"],
        "section": row["section"],
        "content": row["content"],
        "source": "intro",
        "similarity": round(row.get("similarity", 0.0), 4),
        "has_previous": row["chunk_index"] - 1 in indexes,
        "has_next": row["chunk_index"] + 1 in indexes,
    }
