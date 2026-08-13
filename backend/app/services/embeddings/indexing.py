"""为 ready 文档切片持久化增量向量。

Provider、模型、维度和内容哈希共同构成新鲜度契约。任何一项变化只会重新
向量化对应的过期切片，未变化切片会被跳过。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.services.embeddings.contracts import EmbeddingProvider, validate_embedding_vectors


class DocumentNotReadyForEmbeddingError(Exception):
    pass


@dataclass(frozen=True)
class DocumentEmbeddingResult:
    document_id: UUID
    provider_name: str
    model_name: str
    dimension: int
    total_chunks: int
    embedded_chunks: int
    skipped_chunks: int


class DocumentEmbeddingService:
    def __init__(self, batch_size: int | None = None) -> None:
        self.batch_size = batch_size or get_settings().embedding_batch_size

    async def index_document(
        self,
        document: Document,
        session: AsyncSession,
        provider: EmbeddingProvider,
    ) -> DocumentEmbeddingResult:
        """分批向量化过期切片，只有发生变更时才提交事务。"""
        if document.status != "ready":
            raise DocumentNotReadyForEmbeddingError(
                "Document must be parsed successfully before embedding"
            )

        result = await session.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document.id)
            .order_by(DocumentChunk.ordinal)
        )
        chunks = list(result.scalars().all())
        if not chunks:
            raise DocumentNotReadyForEmbeddingError("Document contains no chunks to embed")

        stale_chunks = [
            chunk
            for chunk in chunks
            if chunk.embedding is None
            or chunk.embedding_provider != provider.provider_name
            or chunk.embedding_model != provider.model_name
            or chunk.embedding_dimension != provider.dimension
            or chunk.embedding_content_hash != chunk.content_hash
        ]
        vectors: list[tuple[float, ...]] = []
        for start in range(0, len(stale_chunks), self.batch_size):
            batch = stale_chunks[start : start + self.batch_size]
            batch_vectors = await provider.embed_documents([chunk.content for chunk in batch])
            vectors.extend(
                validate_embedding_vectors(
                    batch_vectors,
                    expected_count=len(batch),
                    expected_dimension=provider.dimension,
                )
            )

        embedded_at = datetime.now(UTC)
        for chunk, vector in zip(stale_chunks, vectors, strict=True):
            chunk.embedding = list(vector)
            chunk.embedding_provider = provider.provider_name
            chunk.embedding_model = provider.model_name
            chunk.embedding_dimension = provider.dimension
            chunk.embedding_content_hash = chunk.content_hash
            chunk.embedded_at = embedded_at

        if stale_chunks:
            await session.commit()

        return DocumentEmbeddingResult(
            document_id=document.id,
            provider_name=provider.provider_name,
            model_name=provider.model_name,
            dimension=provider.dimension,
            total_chunks=len(chunks),
            embedded_chunks=len(stale_chunks),
            skipped_chunks=len(chunks) - len(stale_chunks),
        )


def get_document_embedding_service() -> DocumentEmbeddingService:
    return DocumentEmbeddingService()
