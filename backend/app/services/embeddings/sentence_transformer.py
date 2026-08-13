"""延迟加载本地 BGE/SentenceTransformer 的向量化 Provider 适配器。

模型直到第一次向量化请求才加载，避免导入路由或运行无关测试时无谓加载大型
本地模型。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from threading import Lock
from typing import Any

from app.services.embeddings.contracts import EmbeddingProviderError, validate_embedding_vectors


class SentenceTransformerEmbeddingProvider:
    provider_name = "sentence_transformers"

    def __init__(
        self,
        *,
        model_name: str,
        model_path: str | Path | None,
        dimension: int,
        batch_size: int,
        query_instruction: str,
    ) -> None:
        self.model_name = model_name
        self._model_path = str(model_path) if model_path is not None else None
        self.dimension = dimension
        self.batch_size = batch_size
        self.query_instruction = query_instruction
        self._model: Any | None = None
        self._model_lock = Lock()

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        # 懒加载:被调用时再去硬盘读取、把模型加载出来。线程锁
        with self._model_lock:
            if self._model is not None:
                return self._model
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise EmbeddingProviderError(
                    "Local embedding dependencies are not installed; install the embedding extra"
                ) from exc
            try:
                model = SentenceTransformer(self._model_path or self.model_name, device="cpu")
                actual_dimension = model.get_embedding_dimension()
            except Exception as exc:
                raise EmbeddingProviderError("Local embedding model could not be loaded") from exc
            if actual_dimension != self.dimension:
                raise EmbeddingProviderError(
                    "Configured embedding dimension does not match the model"
                )
            self._model = model
            return model

    def _encode(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        try:
            values = self._get_model().encode(
                list(texts),
                batch_size=self.batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )
        except EmbeddingProviderError:
            raise
        except Exception as exc:
            raise EmbeddingProviderError("Local embedding generation failed") from exc
        return validate_embedding_vectors(
            values,
            expected_count=len(texts),
            expected_dimension=self.dimension,
        )

    async def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        return await asyncio.to_thread(self._encode, texts)

    async def embed_query(self, text: str) -> tuple[float, ...]:
        query = text.strip()
        if not query:
            raise EmbeddingProviderError("Embedding query must not be empty")
        vectors = await asyncio.to_thread(self._encode, (f"{self.query_instruction}{query}",))
        return vectors[0]
