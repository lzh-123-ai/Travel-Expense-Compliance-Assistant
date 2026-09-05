from collections.abc import AsyncIterator, Callable
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.main import app
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.services.document_processing import (
    DocumentProcessingService,
    get_document_processing_service,
)
from app.services.storage import LocalStorage, get_storage_service

KNOWLEDGE_BASE_ID = UUID("2f1c75f4-bf91-4f40-aee7-54152e57b0f8")
OTHER_KNOWLEDGE_BASE_ID = UUID("9a8ef23e-52d9-4803-90de-f6d8ab4bb349")
DOCUMENT_ID = UUID("4a40cf8c-0d67-4e0f-9ef1-1c4d9ec1e7a3")
SUPERSEDED_DOCUMENT_ID = UUID("7e0b6af8-9e13-497f-b484-4178f6400d74")
CHUNK_ID = UUID("1056bc33-2194-4965-b148-9bde6180db44")
SAMPLE_TIME = datetime(2026, 7, 30, 8, 30, tzinfo=UTC)


def override_db_session(
    session: AsyncSession,
) -> Callable[[], AsyncIterator[AsyncSession]]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield session

    return override


def make_document(document_id: UUID = DOCUMENT_ID) -> Document:
    document = Document(
        id=document_id,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        original_filename="travel-policy.md",
        content_type="text/markdown",
        file_size=100,
        storage_key=f"documents/{document_id}.md",
        sha256="a" * 64,
        policy_type="travel_reimbursement",
        version_label="TRAVEL-2025.1",
        effective_from=date(2025, 1, 1),
        effective_to=date(2025, 12, 31),
        access_scope="finance_only",
    )
    document.status = "ready"
    document.error_message = None
    document.supersedes_document_id = None
    document.embedded_image_count = 0
    document.image_only_page_count = 0
    document.text_extraction_status = "complete"
    document.needs_ocr = False
    document.parse_warnings = []
    document.parsed_at = SAMPLE_TIME
    document.created_at = SAMPLE_TIME
    document.updated_at = SAMPLE_TIME
    return document


def scalar_result(value: object) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def configure_app(
    session: AsyncSession,
    storage: LocalStorage,
    processor: DocumentProcessingService | None = None,
) -> None:
    app.dependency_overrides[get_db_session] = override_db_session(session)
    app.dependency_overrides[get_storage_service] = lambda: storage
    if processor is not None:
        app.dependency_overrides[get_document_processing_service] = lambda: processor


def test_metadata_patch_updates_only_explicit_fields(tmp_path: Path) -> None:
    document = make_document()
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = scalar_result(document)
    configure_app(session, LocalStorage(tmp_path))

    try:
        with TestClient(app) as client:
            response = client.patch(
                f"/api/v1/knowledge-bases/{KNOWLEDGE_BASE_ID}/documents/{DOCUMENT_ID}/metadata",
                json={"version_label": "TRAVEL-2025.2"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert document.version_label == "TRAVEL-2025.2"
    assert document.policy_type == "travel_reimbursement"
    assert document.access_scope == "finance_only"
    assert response.json()["access_scope"] == "finance_only"
    session.commit.assert_awaited_once()


def test_metadata_patch_rejects_invalid_range_after_partial_update(tmp_path: Path) -> None:
    document = make_document()
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = scalar_result(document)
    configure_app(session, LocalStorage(tmp_path))

    try:
        with TestClient(app) as client:
            response = client.patch(
                f"/api/v1/knowledge-bases/{KNOWLEDGE_BASE_ID}/documents/{DOCUMENT_ID}/metadata",
                json={"effective_from": "2026-01-01"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["detail"] == "effective_to must not be earlier than effective_from"
    session.commit.assert_not_awaited()


def test_metadata_patch_rejects_self_supersedes(tmp_path: Path) -> None:
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = scalar_result(make_document())
    configure_app(session, LocalStorage(tmp_path))

    try:
        with TestClient(app) as client:
            response = client.patch(
                f"/api/v1/knowledge-bases/{KNOWLEDGE_BASE_ID}/documents/{DOCUMENT_ID}/metadata",
                json={"supersedes_document_id": str(DOCUMENT_ID)},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["detail"] == "A document cannot supersede itself"
    session.commit.assert_not_awaited()


def test_metadata_patch_scopes_superseded_document_to_same_knowledge_base(
    tmp_path: Path,
) -> None:
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [scalar_result(make_document()), scalar_result(None)]
    configure_app(session, LocalStorage(tmp_path))

    try:
        with TestClient(app) as client:
            response = client.patch(
                f"/api/v1/knowledge-bases/{KNOWLEDGE_BASE_ID}/documents/{DOCUMENT_ID}/metadata",
                json={"supersedes_document_id": str(SUPERSEDED_DOCUMENT_ID)},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"] == "Document not found"
    session.commit.assert_not_awaited()


def test_list_chunks_returns_traceability_metadata_in_ordinal_order(tmp_path: Path) -> None:
    document = make_document()
    chunk = DocumentChunk(
        id=CHUNK_ID,
        document_id=DOCUMENT_ID,
        ordinal=0,
        content="上海住宿标准为每晚 500 元。",
        page_start=None,
        page_end=None,
        section_path=["第四章", "住宿费"],
        char_count=16,
        token_estimate=8,
        content_hash="b" * 64,
        extraction_method="native_text",
        chunking_strategy="structure_aware_v1",
        chunk_size=800,
        chunk_overlap=100,
    )
    chunk.created_at = SAMPLE_TIME
    chunks_result = MagicMock()
    chunks_result.scalars.return_value.all.return_value = [chunk]
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [scalar_result(document), chunks_result]
    configure_app(session, LocalStorage(tmp_path))

    try:
        with TestClient(app) as client:
            response = client.get(
                f"/api/v1/knowledge-bases/{KNOWLEDGE_BASE_ID}/documents/{DOCUMENT_ID}/chunks"
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": str(CHUNK_ID),
            "document_id": str(DOCUMENT_ID),
            "ordinal": 0,
            "content": "上海住宿标准为每晚 500 元。",
            "page_start": None,
            "page_end": None,
            "section_path": ["第四章", "住宿费"],
            "char_count": 16,
            "token_estimate": 8,
                "content_hash": "b" * 64,
                "extraction_method": "native_text",
                "source_metadata": {},
                "chunking_strategy": "structure_aware_v1",
            "chunk_size": 800,
            "chunk_overlap": 100,
            "embedding_provider": None,
            "embedding_model": None,
            "embedding_dimension": None,
            "embedding_content_hash": None,
            "embedded_at": None,
            "created_at": "2026-07-30T08:30:00Z",
        }
    ]


def test_process_endpoint_delegates_to_processing_service(tmp_path: Path) -> None:
    document = make_document()
    processor = AsyncMock(spec=DocumentProcessingService)
    processor.process.return_value = document
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = scalar_result(document)
    storage = LocalStorage(tmp_path)
    configure_app(session, storage, processor)

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/knowledge-bases/{KNOWLEDGE_BASE_ID}/documents/{DOCUMENT_ID}/process"
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    processor.process.assert_awaited_once_with(document, session, storage)
    processing_statement = session.execute.await_args.args[0]
    assert processing_statement._for_update_arg is not None


def test_process_endpoint_rejects_document_outside_knowledge_base(tmp_path: Path) -> None:
    processor = AsyncMock(spec=DocumentProcessingService)
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = scalar_result(None)
    configure_app(session, LocalStorage(tmp_path), processor)

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/knowledge-bases/{OTHER_KNOWLEDGE_BASE_ID}/documents/{DOCUMENT_ID}/process"
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found"}
    processor.process.assert_not_awaited()
    processing_statement = session.execute.await_args.args[0]
    assert processing_statement._for_update_arg is not None
