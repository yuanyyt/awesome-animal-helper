"""OpenAI-compatible text embedding integration."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol

from openai import OpenAI

from src.backend.knowledge.config import KnowledgeConfig


class EmbeddingError(RuntimeError):
    """Raised when embeddings cannot be generated or validated."""


class Embedder(Protocol):
    dimensions: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    """Generate embeddings through the configured Bailian-compatible endpoint."""

    def __init__(self, config: KnowledgeConfig) -> None:
        if not config.api_key or not config.embedding_base_url:
            raise EmbeddingError("请配置 DASHSCOPE_API_KEY 和嵌入服务地址")
        if config.embedding_dimensions <= 0:
            raise EmbeddingError("EMBEDDING_DIMENSIONS 必须为正整数")
        self.model = config.embedding_model
        self.dimensions = config.embedding_dimensions
        self.client = OpenAI(
            api_key=config.api_key,
            base_url=config.embedding_base_url,
            timeout=60,
            max_retries=2,
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=list(texts),
                dimensions=self.dimensions,
            )
        except Exception as exc:
            raise EmbeddingError("动物知识向量生成失败") from exc
        vectors = [item.embedding for item in sorted(response.data, key=lambda x: x.index)]
        if len(vectors) != len(texts):
            raise EmbeddingError("嵌入服务返回数量与输入不一致")
        for vector in vectors:
            if len(vector) != self.dimensions or not all(map(math.isfinite, vector)):
                raise EmbeddingError("嵌入服务返回了无效向量")
        return vectors
