"""Command-line management for the persistent animal knowledge database."""

from __future__ import annotations

import argparse

from src.backend.knowledge.service import KnowledgeService


def main() -> None:
    parser = argparse.ArgumentParser(description="管理动物讲解知识库")
    parser.add_argument("command", choices=["rebuild"], help="重建持久化知识索引")
    args = parser.parse_args()
    service = KnowledgeService()
    if args.command == "rebuild":
        service.rebuild()
        print(f"知识库已重建：{service.config.database_path}")


if __name__ == "__main__":
    main()
