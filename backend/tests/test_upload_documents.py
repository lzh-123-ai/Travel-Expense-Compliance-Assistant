from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes import documents
from app.core.config import get_settings
from app.db.session import get_db_session
from app.main import app
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.services.storage import LocalStorage, get_storage_service
from tests.sample_documents import make_generic_zip, make_minimal_docx, make_minimal_pdf

KNOWLEDGE_BASE_ID = UUID("2f1c75f4-bf91-4f40-aee7-54152e57b0f8")
DOCUMENT_ID = UUID("4a40cf8c-0d67-4e0f-9ef1-1c4d9ec1e7a3")
EXISTING_DOCUMENT_ID = UUID("7e0b6af8-9e13-497f-b484-4178f6400d74")
SAMPLE_TIME = datetime(2026, 7, 25, 8, 30, tzinfo=UTC)
SAMPLE_SHA256 = "ed874b308d95db0b0d8e8c9c40e270dda68ec50df2336899da5546479e414aa0"


def override_db_session(
    session: AsyncSession,
) -> Callable[[], AsyncIterator[AsyncSession]]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield session

    return override


def make_session(*, knowledge_base_exists: bool = True) -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = (
        KnowledgeBase(id=KNOWLEDGE_BASE_ID, name="Travel reimbursement")
        if knowledge_base_exists
        else None
    )
    duplicate_result = MagicMock()
    duplicate_result.scalar_one_or_none.return_value = None
    session.execute.return_value = duplicate_result
    session.in_transaction.return_value = False
    return session


def configure_app(session: AsyncSession, storage: LocalStorage) -> None:
    app.dependency_overrides[get_db_session] = override_db_session(session)
    app.dependency_overrides[get_storage_service] = lambda: storage


def populate_database_fields(document: Document) -> None:
    document.status = "pending"
    document.access_scope = "all_employees"
    document.embedded_image_count = 0
    document.image_only_page_count = 0
    document.text_extraction_status = "not_attempted"
    document.needs_ocr = False
    document.parse_warnings = []
    document.created_at = SAMPLE_TIME
    document.updated_at = SAMPLE_TIME


def test_upload_document_persists_file_hash_and_safe_metadata(tmp_path: Path) -> None:
    session = make_session()
    session.refresh.side_effect = populate_database_fields
    storage = LocalStorage(tmp_path)
    configure_app(session, storage)

    try:
        with patch.object(documents, "uuid4", return_value=DOCUMENT_ID):
            with TestClient(app) as client:
                response = client.post(
                    f"/api/v1/knowledge-bases/{KNOWLEDGE_BASE_ID}/documents",
                    files={"file": ("policy.txt", b"keep this safe", "text/plain")},
                )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json() == {
        "id": str(DOCUMENT_ID),
        "knowledge_base_id": str(KNOWLEDGE_BASE_ID),
        "original_filename": "policy.txt",
        "content_type": "text/plain",
        "file_size": 14,
        "sha256": SAMPLE_SHA256,
        "status": "pending",
        "error_message": None,
        "policy_type": None,
        "version_label": None,
        "effective_from": None,
        "effective_to": None,
        "access_scope": "all_employees",
        "supersedes_document_id": None,
        "embedded_image_count": 0,
        "image_only_page_count": 0,
        "text_extraction_status": "not_attempted",
        "needs_ocr": False,
        "parse_warnings": [],
        "parsed_at": None,
        "ocr_status": "not_requested",
        "ocr_provider": None,
        "ocr_model_version": None,
        "ocr_processed_at": None,
        "ocr_low_confidence_page_count": 0,
        "created_at": "2026-07-25T08:30:00Z",
        "updated_at": "2026-07-25T08:30:00Z",
    }

    document = session.add.call_args.args[0]
    assert isinstance(document, Document)
    assert document.storage_key == f"documents/{DOCUMENT_ID}.txt"
    assert (tmp_path / document.storage_key).read_bytes() == b"keep this safe"
    assert document.sha256 == SAMPLE_SHA256
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(document)


def test_upload_document_returns_not_found_without_writing_file(tmp_path: Path) -> None:
    session = make_session(knowledge_base_exists=False)
    configure_app(session, LocalStorage(tmp_path))

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/knowledge-bases/{KNOWLEDGE_BASE_ID}/documents",
                files={"file": ("policy.txt", b"keep this safe", "text/plain")},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Knowledge base not found"}
    assert list(tmp_path.rglob("*")) == []
    session.add.assert_not_called()


def test_upload_document_rejects_oversized_file_and_cleans_up(tmp_path: Path) -> None:
    session = make_session()
    storage = LocalStorage(tmp_path)
    configure_app(session, storage)
    settings = get_settings()
    old_max_size = settings.max_document_size_bytes
    settings.max_document_size_bytes = 3

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/knowledge-bases/{KNOWLEDGE_BASE_ID}/documents",
                files={"file": ("policy.txt", b"four", "text/plain")},
            )
    finally:
        settings.max_document_size_bytes = old_max_size
        app.dependency_overrides.clear()

    assert response.status_code == 413
    assert response.json()["detail"] == "Document exceeds the 3 byte size limit"
    assert list((tmp_path / "documents").glob("*")) == []
    assert list((tmp_path / ".incoming").glob("*")) == []
    session.add.assert_not_called()


def test_upload_document_rejects_extension_and_mime_mismatch(tmp_path: Path) -> None:
    session = make_session()
    configure_app(session, LocalStorage(tmp_path))

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/knowledge-bases/{KNOWLEDGE_BASE_ID}/documents",
                files={"file": ("policy.pdf", b"plain text", "text/plain")},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 415
    assert "does not match .pdf" in response.json()["detail"]
    assert list(tmp_path.rglob("*")) == []


def test_upload_document_rejects_fake_pdf_and_removes_it(tmp_path: Path) -> None:
    session = make_session()
    configure_app(session, LocalStorage(tmp_path))

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/knowledge-bases/{KNOWLEDGE_BASE_ID}/documents",
                files={"file": ("policy.pdf", b"not really a PDF", "application/pdf")},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["detail"] == "File content is not a PDF document"
    assert list((tmp_path / "documents").glob("*")) == []


def test_upload_document_rejects_zip_disguised_as_docx_and_removes_it(tmp_path: Path) -> None:
    session = make_session()
    configure_app(session, LocalStorage(tmp_path))

    fake_docx = make_generic_zip()
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/knowledge-bases/{KNOWLEDGE_BASE_ID}/documents",
                files={
                    "file": (
                        "policy.docx",
                        fake_docx,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["detail"] == "File is a ZIP archive but not a DOCX document"
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []
    session.add.assert_not_called()


def test_upload_document_returns_existing_id_for_duplicate_content(tmp_path: Path) -> None:
    session = make_session()
    duplicate_result = MagicMock()
    duplicate_result.scalar_one_or_none.return_value = EXISTING_DOCUMENT_ID
    session.execute.return_value = duplicate_result
    configure_app(session, LocalStorage(tmp_path))

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/knowledge-bases/{KNOWLEDGE_BASE_ID}/documents",
                files={"file": ("policy.txt", b"keep this safe", "text/plain")},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "message": "Document content already exists",
            "existing_document_id": str(EXISTING_DOCUMENT_ID),
        }
    }
    assert list((tmp_path / "documents").glob("*")) == []
    session.add.assert_not_called()


def test_upload_document_handles_concurrent_duplicate_constraint(tmp_path: Path) -> None:
    session = make_session()
    session.commit.side_effect = IntegrityError("insert", {}, Exception("duplicate"))
    precheck = MagicMock()
    precheck.scalar_one_or_none.return_value = None
    after_conflict = MagicMock()
    after_conflict.scalar_one_or_none.return_value = EXISTING_DOCUMENT_ID
    session.execute.side_effect = [precheck, after_conflict]
    configure_app(session, LocalStorage(tmp_path))

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/knowledge-bases/{KNOWLEDGE_BASE_ID}/documents",
                files={"file": ("policy.txt", b"keep this safe", "text/plain")},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"]["existing_document_id"] == str(EXISTING_DOCUMENT_ID)
    assert list((tmp_path / "documents").glob("*")) == []
    assert session.rollback.await_count >= 1


@pytest.mark.parametrize(
    ("filename", "content_type", "content"),
    [
        ("domestic-travel-policy.pdf", "application/pdf", make_minimal_pdf()),
        (
            "domestic-travel-policy.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            make_minimal_docx(),
        ),
        ("domestic-travel-policy.md", "text/markdown", b"# Hotel reimbursement\n"),
        ("domestic-travel-policy.txt", "text/plain", b"Hotel reimbursement limit\n"),
    ],
)
def test_upload_document_accepts_all_supported_structured_samples(
    tmp_path: Path,
    filename: str,
    content_type: str,
    content: bytes,
) -> None:
    session = make_session()
    session.refresh.side_effect = populate_database_fields
    configure_app(session, LocalStorage(tmp_path))

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/knowledge-bases/{KNOWLEDGE_BASE_ID}/documents",
                files={"file": (filename, content, content_type)},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    document = session.add.call_args.args[0]
    assert (tmp_path / document.storage_key).read_bytes() == content
