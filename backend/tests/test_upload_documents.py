from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db_session
from app.main import app
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase

KNOWLEDGE_BASE_ID = UUID("2f1c75f4-bf91-4f40-aee7-54152e57b0f8")
DOCUMENT_ID = UUID("4a40cf8c-0d67-4e0f-9ef1-1c4d9ec1e7a3")
SAMPLE_TIME = datetime(2026, 7, 25, 8, 30, tzinfo=UTC)


def override_db_session(
    session: AsyncSession,
) -> Callable[[], AsyncIterator[AsyncSession]]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield session

    return override


def test_upload_document_persists_file_and_metadata(tmp_path: Path) -> None:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = KnowledgeBase(id=KNOWLEDGE_BASE_ID, name="Security")

    async def populate_database_fields(document: Document) -> None:
        document.id = DOCUMENT_ID
        document.status = "pending"
        document.created_at = SAMPLE_TIME
        document.updated_at = SAMPLE_TIME

    session.refresh.side_effect = populate_database_fields
    settings = get_settings()
    old_upload_dir = settings.upload_dir
    settings.upload_dir = tmp_path
    app.dependency_overrides[get_db_session] = override_db_session(session)

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/knowledge-bases/{KNOWLEDGE_BASE_ID}/documents",
                files={"file": ("policy.txt", b"keep this safe", "text/plain")},
            )
    finally:
        settings.upload_dir = old_upload_dir
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json() == {
        "id": str(DOCUMENT_ID),
        "knowledge_base_id": str(KNOWLEDGE_BASE_ID),
        "original_filename": "policy.txt",
        "content_type": "text/plain",
        "file_size": 14,
        "status": "pending",
        "error_message": None,
        "created_at": "2026-07-25T08:30:00Z",
        "updated_at": "2026-07-25T08:30:00Z",
    }

    document = session.add.call_args.args[0]
    assert isinstance(document, Document)
    stored_file = Path(document.storage_path)
    assert stored_file.read_bytes() == b"keep this safe"
    assert document.knowledge_base_id == KNOWLEDGE_BASE_ID
    assert document.status == "pending"
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(document)

    stored_file.unlink()


def test_upload_document_returns_not_found_without_writing_file(tmp_path: Path) -> None:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = None
    settings = get_settings()
    old_upload_dir = settings.upload_dir
    settings.upload_dir = tmp_path
    app.dependency_overrides[get_db_session] = override_db_session(session)

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/knowledge-bases/{KNOWLEDGE_BASE_ID}/documents",
                files={"file": ("policy.txt", b"keep this safe", "text/plain")},
            )
    finally:
        settings.upload_dir = old_upload_dir
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Knowledge base not found"}
    assert list(tmp_path.iterdir()) == []
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


def test_upload_document_rejects_oversized_file_and_cleans_up(tmp_path: Path) -> None:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = KnowledgeBase(id=KNOWLEDGE_BASE_ID, name="Security")
    settings = get_settings()
    old_upload_dir = settings.upload_dir
    old_max_size = settings.max_document_size_bytes
    settings.upload_dir = tmp_path
    settings.max_document_size_bytes = 3
    app.dependency_overrides[get_db_session] = override_db_session(session)

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/knowledge-bases/{KNOWLEDGE_BASE_ID}/documents",
                files={"file": ("policy.txt", b"four", "text/plain")},
            )
    finally:
        settings.upload_dir = old_upload_dir
        settings.max_document_size_bytes = old_max_size
        app.dependency_overrides.clear()

    assert response.status_code == 413
    assert response.json()["detail"] == "Document exceeds the 3 byte size limit"
    assert list(tmp_path.iterdir()) == []
    session.add.assert_not_called()
    session.commit.assert_not_awaited()
