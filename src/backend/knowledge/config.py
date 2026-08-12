"""Configuration for the persistent animal knowledge database."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
RUNTIME_DIR = Path(os.getenv("APP_RUNTIME_DIR", DATA_DIR / "runtime")).expanduser()


@dataclass(frozen=True)
class KnowledgeConfig:
    """Paths and embedding settings used to build the knowledge database."""

    database_path: Path = RUNTIME_DIR / "knowledge.db"
    animals_path: Path = DATA_DIR / "animals.csv"
    sites_path: Path = DATA_DIR / "animal_sites.xlsx"
    intro_path: Path = DATA_DIR / "intro.md"
    embedding_model: str = "text-embedding-v4"
    embedding_dimensions: int = 1024
    embedding_base_url: str = ""
    api_key: str = ""

    @classmethod
    def from_env(cls) -> "KnowledgeConfig":
        load_dotenv()
        return cls(
            embedding_model=os.getenv("EMBEDDING_MODEL", "").strip()
            or "text-embedding-v4",
            embedding_dimensions=int(
                os.getenv("EMBEDDING_DIMENSIONS", "").strip() or "1024"
            ),
            embedding_base_url=(
                os.getenv("EMBEDDING_BASE_URL", "").strip()
                or os.getenv("LLM_BASE_URL", "").strip()
            ),
            api_key=os.getenv("DASHSCOPE_API_KEY", "").strip(),
        )
