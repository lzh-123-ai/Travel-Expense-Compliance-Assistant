from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.services.chunking import DeterministicChunker
from app.services.document_validation import DocumentValidationError, validate_upload_metadata
from app.services.parsing import (
    DocumentParseError,
    ParsedDocument,
    ParserRegistry,
    ParseWarning,
    TextExtractionStatus,
    create_default_parser_registry,
)
from app.services.storage import StorageService

logger = logging.getLogger(__name__)


class DocumentProcessingService:
    """编排解析、切片和状态持久化；解析器本身保持数据库无关。"""

    def __init__(
        self,
        registry: ParserRegistry | None = None,
        chunker: DeterministicChunker | None = None,
    ) -> None:
        self.registry = registry or create_default_parser_registry()
        self.chunker = chunker or DeterministicChunker()

    async def process(
        self,
        document: Document,
        session: AsyncSession,
        storage: StorageService,
    ) -> Document:
        document.status = "processing"
        document.error_message = None
        await session.flush()

        try:
            metadata = validate_upload_metadata(
                document.original_filename,
                document.content_type,
            )
            with storage.open(document.storage_key) as source:
                parsed = self.registry.parse(metadata.document_format, source)
        except DocumentParseError as exc:
            await self._replace_chunks(session, document, ())
            self._mark_failed(
                document,
                TextExtractionStatus.FAILED,
                exc.safe_message,
                (ParseWarning(exc.code, exc.safe_message),),
            )
            await session.commit()
            await session.refresh(document)
            return document
        except (DocumentValidationError, OSError, ValueError):
            logger.exception("Document processing failed before parsing")
            await self._replace_chunks(session, document, ())
            self._mark_failed(
                document,
                TextExtractionStatus.FAILED,
                "Document could not be processed",
                (ParseWarning("processing_error", "Document could not be processed"),),
            )
            await session.commit()
            await session.refresh(document)
            return document
        except Exception:
            # 未知解析库异常只进入服务端日志，避免把路径、堆栈或底层对象泄露给用户。
            logger.exception("Unexpected document parser failure")
            await self._replace_chunks(session, document, ())
            self._mark_failed(
                document,
                TextExtractionStatus.FAILED,
                "Document could not be parsed",
                (ParseWarning("parser_error", "Document could not be parsed"),),
            )
            await session.commit()
            await session.refresh(document)
            return document

        self._apply_parse_metadata(document, parsed)
        if not parsed.sections:
            await self._replace_chunks(session, document, ())
            document.status = "failed"
            document.error_message = "Document contains no extractable text"
        else:
            drafts = self.chunker.chunk(parsed)
            chunks = tuple(
                DocumentChunk(
                    document_id=document.id,
                    ordinal=draft.ordinal,
                    content=draft.content,
                    page_start=draft.page_start,
                    page_end=draft.page_end,
                    section_path=list(draft.section_path),
                    char_count=draft.char_count,
                    token_estimate=draft.token_estimate,
                    content_hash=draft.content_hash,
                    extraction_method=draft.extraction_method,
                    chunking_strategy=draft.chunking_strategy,
                    chunk_size=draft.chunk_size,
                    chunk_overlap=draft.chunk_overlap,
                )
                for draft in drafts
            )
            await self._replace_chunks(session, document, chunks)
            document.status = "ready"
            document.error_message = None

        await session.commit()
        await session.refresh(document)
        return document

    @staticmethod
    async def _replace_chunks(
        session: AsyncSession,
        document: Document,
        chunks: tuple[DocumentChunk, ...],
    ) -> None:
        await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
        session.add_all(chunks)

    @staticmethod
    def _apply_parse_metadata(document: Document, parsed: ParsedDocument) -> None:
        document.embedded_image_count = parsed.embedded_image_count
        document.image_only_page_count = parsed.image_only_page_count
        document.text_extraction_status = parsed.text_extraction_status
        document.needs_ocr = parsed.needs_ocr
        document.parse_warnings = parsed.serialized_warnings()
        document.parsed_at = datetime.now(UTC)

    @staticmethod
    def _mark_failed(
        document: Document,
        extraction_status: TextExtractionStatus,
        message: str,
        warnings: tuple[ParseWarning, ...],
    ) -> None:
        document.status = "failed"
        document.error_message = message
        document.text_extraction_status = extraction_status
        document.embedded_image_count = 0
        document.image_only_page_count = 0
        document.needs_ocr = False
        document.parse_warnings = [warning.as_dict() for warning in warnings]
        document.parsed_at = datetime.now(UTC)


def get_document_processing_service() -> DocumentProcessingService:
    return DocumentProcessingService()
