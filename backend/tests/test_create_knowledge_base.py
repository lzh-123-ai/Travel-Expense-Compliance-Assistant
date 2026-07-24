from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.main import app
from app.models.knowledge_base import KnowledgeBase

SAMPLE_ID = UUID("2f1c75f4-bf91-4f40-aee7-54152e57b0f8")
SAMPLE_TIME = datetime(2026, 7, 24, 8, 30, tzinfo=UTC)


def override_db_session(
    session: AsyncSession,
) -> Callable[[], AsyncIterator[AsyncSession]]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield session

    return override


def test_create_knowledge_base_returns_created_resource() -> None:
    session = AsyncMock(spec=AsyncSession)

    async def populate_database_fields(knowledge_base: KnowledgeBase) -> None:
        knowledge_base.id = SAMPLE_ID
        knowledge_base.created_at = SAMPLE_TIME
        knowledge_base.updated_at = SAMPLE_TIME

    session.refresh.side_effect = populate_database_fields
    app.dependency_overrides[get_db_session] = override_db_session(session)

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/knowledge-bases",
                json={
                    "name": "  Human resources  ",
                    "description": "  Employee policies and procedures  ",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json() == {
        "id": str(SAMPLE_ID),
        "name": "Human resources",
        "description": "Employee policies and procedures",
        "created_at": "2026-07-24T08:30:00Z",
        "updated_at": "2026-07-24T08:30:00Z",
    }

    created_knowledge_base = session.add.call_args.args[0]
    assert isinstance(created_knowledge_base, KnowledgeBase)
    assert created_knowledge_base.name == "Human resources"
    session.add.assert_called_once()
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(created_knowledge_base)
    session.rollback.assert_not_awaited()


def test_create_knowledge_base_returns_conflict_for_duplicate_name() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.commit.side_effect = IntegrityError(
        statement="INSERT INTO knowledge_bases ...",
        params={},
        orig=Exception("duplicate key"),
    )
    app.dependency_overrides[get_db_session] = override_db_session(session)

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/knowledge-bases",
                json={"name": "Human resources"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json() == {"detail": "Knowledge base name already exists"}
    session.add.assert_called_once()
    session.commit.assert_awaited_once()
    session.rollback.assert_awaited_once()
    session.refresh.assert_not_awaited()


def test_create_knowledge_base_rejects_blank_name() -> None:
    session = AsyncMock(spec=AsyncSession)
    app.dependency_overrides[get_db_session] = override_db_session(session)

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/knowledge-bases",
                json={"name": " "},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


def test_create_knowledge_base_rejects_unknown_fields() -> None:
    session = AsyncMock(spec=AsyncSession)
    app.dependency_overrides[get_db_session] = override_db_session(session)

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/knowledge-bases",
                json={
                    "name": "Information security",
                    "descripttion": "This field name is misspelled",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"
    assert response.json()["detail"][0]["loc"] == ["body", "descripttion"]
    session.add.assert_not_called()
    session.commit.assert_not_awaited()
