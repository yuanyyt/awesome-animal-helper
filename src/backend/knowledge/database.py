"""SQLite schema and queries for persistent animal knowledge."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import sqlite_vec

from src.backend.domain.models import AnimalDetail


class KnowledgeDatabaseError(RuntimeError):
    """Raised when the knowledge database is unavailable or malformed."""


class KnowledgeDatabase:
    """Read and write the relational and vector indexes in one SQLite file."""

    def __init__(self, path: Path, dimensions: int) -> None:
        self.path = path
        self.dimensions = dimensions

    def ready(self) -> bool:
        if not self.path.is_file():
            return False
        try:
            with self.connect() as connection:
                row = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'initialized'"
                ).fetchone()
                return row is not None and row[0] == "1"
        except (sqlite3.Error, OSError):
            return False

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
        connection.enable_load_extension(False)
        return connection

    def create_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            f"""
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE animals (
                name TEXT PRIMARY KEY,
                scientific_name TEXT,
                taxonomy TEXT,
                habitat TEXT,
                distribution TEXT,
                diet TEXT,
                behavior TEXT,
                reproduction TEXT,
                conservation_status TEXT,
                fun_facts TEXT NOT NULL,
                source_url TEXT,
                language TEXT,
                data_status TEXT NOT NULL
            );
            CREATE TABLE sites (
                name TEXT PRIMARY KEY,
                sort_order INTEGER NOT NULL
            );
            CREATE TABLE animal_sites (
                animal_name TEXT NOT NULL REFERENCES animals(name) ON DELETE CASCADE,
                site_name TEXT NOT NULL REFERENCES sites(name) ON DELETE CASCADE,
                PRIMARY KEY (animal_name, site_name)
            );
            CREATE INDEX animal_sites_site_idx ON animal_sites(site_name);
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY,
                document_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                section TEXT NOT NULL,
                content TEXT NOT NULL,
                UNIQUE(document_id, chunk_index)
            );
            CREATE INDEX chunks_section_idx ON chunks(section);
            CREATE TABLE chunk_animals (
                chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
                animal_name TEXT NOT NULL REFERENCES animals(name) ON DELETE CASCADE,
                PRIMARY KEY (chunk_id, animal_name)
            );
            CREATE INDEX chunk_animals_name_idx ON chunk_animals(animal_name, chunk_id);
            CREATE VIRTUAL TABLE vec_chunks USING vec0(
                embedding float[{self.dimensions}]
            );
            """
        )

    def insert_animals(
        self,
        connection: sqlite3.Connection,
        animals: Sequence[AnimalDetail],
        site_names: Sequence[str],
    ) -> None:
        connection.executemany(
            """
            INSERT INTO animals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item.name,
                    item.scientific_name,
                    item.taxonomy,
                    item.habitat,
                    item.distribution,
                    item.diet,
                    item.behavior,
                    item.reproduction,
                    item.conservation_status,
                    json.dumps(item.fun_facts, ensure_ascii=False),
                    item.source_url,
                    item.language,
                    item.data_status,
                )
                for item in animals
            ],
        )
        connection.executemany(
            "INSERT INTO sites(name, sort_order) VALUES (?, ?)",
            [(name, index) for index, name in enumerate(site_names)],
        )
        connection.executemany(
            "INSERT OR IGNORE INTO animal_sites VALUES (?, ?)",
            [
                (animal.name, site)
                for animal in animals
                for site in animal.sites
                if site in site_names
            ],
        )

    def animal_records(self, names: Sequence[str]) -> list[dict[str, Any]]:
        if not names:
            return []
        placeholders = ",".join("?" for _ in names)
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM animals WHERE name IN ({placeholders})", tuple(names)
            ).fetchall()
            sites = connection.execute(
                f"""
                SELECT animal_name, site_name FROM animal_sites
                WHERE animal_name IN ({placeholders})
                ORDER BY animal_name
                """,
                tuple(names),
            ).fetchall()
        by_name = {row["name"]: dict(row) for row in rows}
        for item in by_name.values():
            item["fun_facts"] = json.loads(item["fun_facts"])
            item["sites"] = []
        for row in sites:
            by_name[row["animal_name"]]["sites"].append(row["site_name"])
        return [by_name[name] for name in names if name in by_name]

    def mapped_animals(self, chunk_ids: Sequence[int], limit: int = 8) -> list[str]:
        if not chunk_ids:
            return []
        placeholders = ",".join("?" for _ in chunk_ids)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT animal_name, MIN(chunk_id) AS first_chunk
                FROM chunk_animals WHERE chunk_id IN ({placeholders})
                GROUP BY animal_name ORDER BY first_chunk LIMIT ?
                """,
                (*chunk_ids, limit),
            ).fetchall()
        return [row["animal_name"] for row in rows]

    def exact_chunks(self, names: Sequence[str], limit: int = 60) -> list[dict[str, Any]]:
        if not names:
            return []
        placeholders = ",".join("?" for _ in names)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT DISTINCT c.* FROM chunks c
                JOIN chunk_animals ca ON ca.chunk_id = c.id
                WHERE ca.animal_name IN ({placeholders})
                ORDER BY CASE WHEN c.section IN ({placeholders}) THEN 0 ELSE 1 END,
                         c.chunk_index
                LIMIT ?
                """,
                (*names, *names, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def vector_chunks(
        self,
        vector: Sequence[float],
        names: Sequence[str],
        limit: int = 24,
    ) -> list[dict[str, Any]]:
        serialized = sqlite_vec.serialize_float32(list(vector))
        params: list[Any] = [serialized]
        join = ""
        where = ""
        if names:
            placeholders = ",".join("?" for _ in names)
            join = "JOIN chunk_animals ca ON ca.chunk_id = c.id"
            where = f"WHERE ca.animal_name IN ({placeholders})"
            params.extend(names)
        params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT DISTINCT c.*,
                       1.0 - vec_distance_cosine(v.embedding, ?) AS similarity
                FROM vec_chunks v JOIN chunks c ON c.id = v.rowid
                {join} {where}
                ORDER BY similarity DESC LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def neighboring_chunks(
        self,
        chunk_ids: Sequence[int],
        before: int,
        after: int,
    ) -> list[dict[str, Any]]:
        if not chunk_ids:
            return []
        placeholders = ",".join("?" for _ in chunk_ids)
        with self.connect() as connection:
            targets = connection.execute(
                f"SELECT * FROM chunks WHERE id IN ({placeholders})", tuple(chunk_ids)
            ).fetchall()
            chunks: dict[int, dict[str, Any]] = {}
            for target in targets:
                rows = connection.execute(
                    """
                    SELECT * FROM chunks
                    WHERE document_id = ? AND section = ?
                      AND chunk_index BETWEEN ? AND ?
                    ORDER BY chunk_index
                    """,
                    (
                        target["document_id"],
                        target["section"],
                        target["chunk_index"] - before,
                        target["chunk_index"] + after,
                    ),
                ).fetchall()
                chunks.update((row["id"], dict(row)) for row in rows)
        return sorted(chunks.values(), key=lambda row: (row["document_id"], row["chunk_index"]))


def insert_chunks(
    connection: sqlite3.Connection,
    chunks: Iterable[dict[str, Any]],
    vectors: Sequence[Sequence[float]],
) -> None:
    rows = list(chunks)
    for chunk, vector in zip(rows, vectors, strict=True):
        cursor = connection.execute(
            """
            INSERT INTO chunks(document_id, chunk_index, section, content)
            VALUES (?, ?, ?, ?)
            """,
            (
                chunk["document_id"],
                chunk["chunk_index"],
                chunk["section"],
                chunk["content"],
            ),
        )
        chunk_id = cursor.lastrowid
        connection.execute(
            "INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
            (chunk_id, sqlite_vec.serialize_float32(list(vector))),
        )
        connection.executemany(
            "INSERT INTO chunk_animals(chunk_id, animal_name) VALUES (?, ?)",
            [(chunk_id, name) for name in chunk["animal_names"]],
        )
