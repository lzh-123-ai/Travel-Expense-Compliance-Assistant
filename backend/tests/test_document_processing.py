from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.services.document_processing import DocumentProcessingService
from app.services.parsing import (
    DocumentParseError,
    ParsedDocument,
    ParsedSection,
    ParseFailureCode,
    TextExtractionStatus,
)
from app.services.storage import LocalStorage

DOCUMENT_ID = UUID("4a40cf8c-0d67-4e0f-9ef1-1c4d9ec1e7a3")
KNOWLEDGE_BASE_ID = UUID("2f1c75f4-bf91-4f40-aee7-54152e57b0f8")


def make_document(tmp_path: Path) -> tuple[Document, LocalStorage]:
    document = Document(
        id=DOCUMENT_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        original_filename="policy.txt",
        content_type="text/plain",
        file_size=12,
        storage_key=f"documents/{DOCUMENT_ID}.txt",
        sha256="a" * 64,
    )
    document.status = "pending"
    document.text_extraction_status = "not_attempted"
    destination = tmp_path / document.storage_key
    destination.parent.mkdir(parents=True)
    destination.write_text("住宿费制度正文", encoding="utf-8")
    return document, LocalStorage(tmp_path)


def make_session() -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = MagicMock()
    return session


@pytest.mark.asyncio
async def test_processing_replaces_chunks_and_marks_ready(tmp_path: Path) -> None:
    document, storage = make_document(tmp_path)
    session = make_session()

    await DocumentProcessingService().process(document, session, storage)

    assert document.status == "ready"
    assert document.text_extraction_status == "complete"
    assert document.error_message is None
    assert document.parsed_at is not None
    chunks = session.add_all.call_args.args[0]
    assert len(chunks) == 1
    assert chunks[0].content == "住宿费制度正文"
    assert chunks[0].document_id == DOCUMENT_ID
    session.flush.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reprocessing_deletes_old_chunks_before_inserting_new_ones(tmp_path: Path) -> None:
    document, storage = make_document(tmp_path)
    session = make_session()
    service = DocumentProcessingService()

    await service.process(document, session, storage)
    await service.process(document, session, storage)

    assert session.execute.await_count == 2
    assert session.add_all.call_count == 2
    assert session.commit.await_count == 2


@pytest.mark.asyncio
async def test_no_text_document_is_failed_and_creates_no_chunks(tmp_path: Path) -> None:
    document, storage = make_document(tmp_path)
    registry = MagicMock()
    registry.parse.return_value = ParsedDocument(
        sections=(),
        text_extraction_status=TextExtractionStatus.NO_TEXT,
        image_only_page_numbers=(1,),
        page_count=1,
        requires_ocr=True,
        parser_name="fixture",
    )
    session = make_session()

    await DocumentProcessingService(registry=registry).process(document, session, storage)

    assert document.status == "failed"
    assert document.text_extraction_status == "no_text"
    assert document.needs_ocr is True
    assert document.image_only_page_count == 1
    assert document.error_message == "Document contains no extractable text"
    session.add_all.assert_called_once_with(())


@pytest.mark.asyncio
async def test_mixed_pdf_is_ready_but_partial_and_only_stores_text_chunks(
    tmp_path: Path,
) -> None:
    document, storage = make_document(tmp_path)
    registry = MagicMock()
    registry.parse.return_value = ParsedDocument(
        sections=(ParsedSection(order=0, text="第一页正文", page_number=1),),
        text_extraction_status=TextExtractionStatus.PARTIAL,
        image_only_page_numbers=(2,),
        page_count=2,
        parser_name="fixture",
    )
    session = make_session()

    await DocumentProcessingService(registry=registry).process(document, session, storage)

    assert document.status == "ready"
    assert document.text_extraction_status == "partial"
    assert document.needs_ocr is True
    chunks = session.add_all.call_args.args[0]
    assert [chunk.page_start for chunk in chunks] == [1]


@pytest.mark.asyncio
async def test_parser_failure_saves_only_safe_error(tmp_path: Path) -> None:
    document, storage = make_document(tmp_path)
    registry = MagicMock()
    registry.parse.side_effect = DocumentParseError(
        ParseFailureCode.CORRUPT_DOCUMENT,
        "PDF document could not be parsed",
    )
    session = make_session()

    await DocumentProcessingService(registry=registry).process(document, session, storage)

    assert document.status == "failed"
    assert document.text_extraction_status == "failed"
    assert document.error_message == "PDF document could not be parsed"
    assert "Traceback" not in document.error_message
    assert document.parse_warnings[0]["code"] == "corrupt_document"
