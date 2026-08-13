"""文档解析编排服务。

由处理路由获取行锁后调用。该服务把一个已存储文件转换为可追溯切片，同时让
解析器不依赖 SQLAlchemy 和具体存储策略。
"""

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
        """在一个事务中替换文档的全部派生切片。

        ``ready`` 只表示文本解析和切片持久化完成，并不代表 OCR 完成；解析器
        警告会保留这一限制。所有失败路径都会先删除旧切片，避免解析失败后仍能
        检索到过期证据。
        """
        # 路由获取的行锁可阻止第二个 Worker 同时重建同一文档和竞争切片序号。
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
            # 解析器错误携带的是可安全持久化的提示文本。
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
            # 基础设施和第三方库细节只记录服务端日志，不写入用户可见元数据。
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

        # 分支前先保存解析溯源信息，所有终态都可通过文档记录审计。
        self._apply_parse_metadata(document, parsed)
        if not parsed.sections:
            await self._replace_chunks(session, document, ())
            document.status = "failed"
            document.error_message = "Document contains no extractable text"
        else:
            # 切片是派生数据：替换而非追加，使重试幂等且不产生重复语义证据。
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
        """原子替换一个文档下的全部派生切片。"""
        await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
        session.add_all(chunks)

    @staticmethod
    def _apply_parse_metadata(document: Document, parsed: ParsedDocument) -> None:
        """将解析结果和 OCR 警告复制到可持久化的文档元数据。"""
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
        """记录安全的失败消息；原始解析细节仅保留在服务端日志。"""
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
