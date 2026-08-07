from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.services.embeddings import EmbeddingProvider, validate_embedding_vectors

PUBLIC_DOCUMENT_SCOPES = frozenset({"all_employees"})


@dataclass(frozen=True)
class DenseSearchHit:
    chunk_id: UUID
    document_id: UUID
    original_filename: str
    version_label: str | None
    content: str
    page_start: int | None
    page_end: int | None
    section_path: tuple[str, ...]
    cosine_distance: float

    @property
    def similarity(self) -> float:
        return 1.0 - self.cosine_distance


class DenseRetrievalService:
    async def search(
        self,
        session: AsyncSession,
        provider: EmbeddingProvider,
        *,
        knowledge_base_id: UUID,
        query: str,
        expense_date: date,
        allowed_scopes: frozenset[str],
        top_k: int,
    ) -> tuple[DenseSearchHit, ...]:
        if not allowed_scopes:
            return ()
        query_vector = await provider.embed_query(query)
        vector = validate_embedding_vectors(
            (query_vector,), expected_count=1, expected_dimension=provider.dimension
        )[0]
        distance = DocumentChunk.embedding.cosine_distance(list(vector))
        statement = (
            select(DocumentChunk, Document, distance.label("cosine_distance"))
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                Document.knowledge_base_id == knowledge_base_id,
                Document.status == "ready",
                Document.access_scope.in_(allowed_scopes),
                or_(Document.effective_from.is_(None), Document.effective_from <= expense_date),
                or_(Document.effective_to.is_(None), Document.effective_to >= expense_date),
                DocumentChunk.embedding.is_not(None),
                DocumentChunk.embedding_provider == provider.provider_name,
                DocumentChunk.embedding_model == provider.model_name,
                DocumentChunk.embedding_dimension == provider.dimension,
                DocumentChunk.embedding_content_hash == DocumentChunk.content_hash,
            )
            .order_by(distance, DocumentChunk.document_id, DocumentChunk.ordinal)
            .limit(top_k)
        )
        result = await session.execute(statement)
        return tuple(
            DenseSearchHit(
                chunk_id=chunk.id,
                document_id=document.id,
                original_filename=document.original_filename,
                version_label=document.version_label,
                content=chunk.content,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                section_path=tuple(chunk.section_path),
                cosine_distance=float(cosine_distance),
            )
            for chunk, document, cosine_distance in result.all()
        )


def get_dense_retrieval_service() -> DenseRetrievalService:
    return DenseRetrievalService()
