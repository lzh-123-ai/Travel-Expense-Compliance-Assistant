from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.main import app
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.services.storage import LocalStorage, get_storage_service

KNOWLEDGE_BASE_ID = UUID("2f1c75f4-bf91-4f40-aee7-54152e57b0f8")
OTHER_KNOWLEDGE_BASE_ID = UUID("d1f0fc55-3839-443d-a112-eb2deee6390a")
DOCUMENT_ID = UUID("4a40cf8c-0d67-4e0f-9ef1-1c4d9ec1e7a3")
SAMPLE_TIME = datetime(2026, 7, 25, 8, 30, tzinfo=UTC)


def override_db_session(
    session: AsyncSession,
) -> Callable[[], AsyncIterator[AsyncSession]]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield session

    return override


def make_document() -> Document:
    document = Document(
        id=DOCUMENT_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        original_filename="travel-policy.txt",
        content_type="text/plain",
        file_size=6,
        storage_key=f"documents/{DOCUMENT_ID}.txt",
        sha256="a" * 64,
    )
    document.status = "pending"
    document.error_message = None
    document.created_at = SAMPLE_TIME
    document.updated_at = SAMPLE_TIME
    return document


def configure_app(session: AsyncSession, storage: LocalStorage) -> None:
    app.dependency_overrides[get_db_session] = override_db_session(session)
    app.dependency_overrides[get_storage_service] = lambda: storage


def response_payload() -> dict[str, object]:
    return {
        "id": str(DOCUMENT_ID),
        "knowledge_base_id": str(KNOWLEDGE_BASE_ID),
        "original_filename": "travel-policy.txt",
        "content_type": "text/plain",
        "file_size": 6,
        "sha256": "a" * 64,
        "status": "pending",
        "error_message": None,
        "created_at": "2026-07-25T08:30:00Z",
        "updated_at": "2026-07-25T08:30:00Z",
    }


def test_list_documents_returns_rows_for_knowledge_base(tmp_path: Path) -> None:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = KnowledgeBase(id=KNOWLEDGE_BASE_ID, name="Travel")
    result = MagicMock()
    result.scalars.return_value.all.return_value = [make_document()]
    session.execute.return_value = result
    configure_app(session, LocalStorage(tmp_path))

    try:
        with TestClient(app) as client:
            response = client.get(f"/api/v1/knowledge-bases/{KNOWLEDGE_BASE_ID}/documents")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == [response_payload()]


def test_get_document_is_scoped_to_knowledge_base(tmp_path: Path) -> None:
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result
    configure_app(session, LocalStorage(tmp_path))

    try:
        with TestClient(app) as client:
            response = client.get(
                f"/api/v1/knowledge-bases/{OTHER_KNOWLEDGE_BASE_ID}/documents/{DOCUMENT_ID}"
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found"}


def test_delete_document_removes_database_row_and_file(tmp_path: Path) -> None:
    document = make_document()
    source = tmp_path / document.storage_key
    source.parent.mkdir(parents=True)
    source.write_text("policy", encoding="utf-8")
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.scalar_one_or_none.return_value = document
    session.execute.return_value = result
    configure_app(session, LocalStorage(tmp_path))

    try:
        with TestClient(app) as client:
            response = client.delete(
                f"/api/v1/knowledge-bases/{KNOWLEDGE_BASE_ID}/documents/{DOCUMENT_ID}"
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204
    assert response.content == b""
    assert not source.exists()
    session.delete.assert_awaited_once_with(document)
    session.commit.assert_awaited_once()


def test_delete_document_restores_file_when_commit_fails(tmp_path: Path) -> None:
    document = make_document()
    source = tmp_path / document.storage_key
    source.parent.mkdir(parents=True)
    source.write_text("policy", encoding="utf-8")
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.scalar_one_or_none.return_value = document
    session.execute.return_value = result
    session.commit.side_effect = SQLAlchemyError("database unavailable")
    configure_app(session, LocalStorage(tmp_path))

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.delete(
                f"/api/v1/knowledge-bases/{KNOWLEDGE_BASE_ID}/documents/{DOCUMENT_ID}"
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert source.read_text(encoding="utf-8") == "policy"
    session.rollback.assert_awaited_once()
