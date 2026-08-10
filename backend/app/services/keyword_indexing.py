from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.services.keywording import KEYWORD_TOKENIZER_VERSION, searchable_text


class DocumentNotReadyForKeywordIndexingError(Exception):
    pass


@dataclass(frozen=True)
class DocumentKeywordIndexResult:
    document_id: UUID
    tokenizer: str
    total_chunks: int
    indexed_chunks: int
    skipped_chunks: int


class DocumentKeywordIndexingService:
    def __init__(self, tokenizer: str = KEYWORD_TOKENIZER_VERSION) -> None:
        self.tokenizer = tokenizer

    async def index_document(
        self,
        document: Document,
        session: AsyncSession,
    ) -> DocumentKeywordIndexResult:
        if document.status != "ready":
            raise DocumentNotReadyForKeywordIndexingError(
                "Document must be parsed successfully before keyword indexing"
            )

        result = await session.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document.id)
            .order_by(DocumentChunk.ordinal)
        )
        chunks = list(result.scalars().all())
        if not chunks:
            raise DocumentNotReadyForKeywordIndexingError("Document contains no chunks to index")

        stale_chunks = [
            chunk
            for chunk in chunks
            if chunk.keyword_search_text is None
            or chunk.keyword_tokenizer != self.tokenizer
            or chunk.keyword_content_hash != chunk.content_hash
        ]
        indexed_at = datetime.now(UTC)
        for chunk in stale_chunks:
            text = searchable_text(chunk.content)
            if not text:
                raise DocumentNotReadyForKeywordIndexingError(
                    "Document contains a chunk with no searchable terms"
                )
            chunk.keyword_search_text = text
            chunk.keyword_tokenizer = self.tokenizer
            chunk.keyword_content_hash = chunk.content_hash
            chunk.keyword_indexed_at = indexed_at

        if stale_chunks:
            await session.commit()

        return DocumentKeywordIndexResult(
            document_id=document.id,
            tokenizer=self.tokenizer,
            total_chunks=len(chunks),
            indexed_chunks=len(stale_chunks),
            skipped_chunks=len(chunks) - len(stale_chunks),
        )


def get_document_keyword_indexing_service() -> DocumentKeywordIndexingService:
    return DocumentKeywordIndexingService(tokenizer=get_settings().keyword_tokenizer_version)
