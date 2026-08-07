from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol


class EmbeddingProviderError(Exception):
    """可安全返回给编排层的 embedding 失败，不泄露模型缓存和底层堆栈。"""


class EmbeddingProvider(Protocol):
    provider_name: str
    model_name: str
    dimension: int

    async def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...

    async def embed_query(self, text: str) -> tuple[float, ...]: ...


def validate_embedding_vectors(
    vectors: Sequence[Sequence[float]],
    *,
    expected_count: int,
    expected_dimension: int,
) -> tuple[tuple[float, ...], ...]:
    if len(vectors) != expected_count:
        raise EmbeddingProviderError("Embedding provider returned an unexpected vector count")

    normalized: list[tuple[float, ...]] = []
    for vector in vectors:
        values = tuple(float(value) for value in vector)
        if len(values) != expected_dimension:
            raise EmbeddingProviderError("Embedding provider returned an unexpected dimension")
        if not all(math.isfinite(value) for value in values):
            raise EmbeddingProviderError("Embedding provider returned a non-finite value")
        if not any(value != 0 for value in values):
            raise EmbeddingProviderError("Embedding provider returned a zero vector")
        normalized.append(values)
    return tuple(normalized)
