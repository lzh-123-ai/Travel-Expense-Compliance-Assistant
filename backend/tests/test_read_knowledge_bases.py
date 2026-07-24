from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.main import app
from app.models.knowledge_base import KnowledgeBase

FIRST_ID = UUID("9fa276c7-d6dd-4c28-a216-48fd10b6f223")
SECOND_ID = UUID("1957acc8-a6a8-49d7-91e0-5c1df890543e")
FIRST_TIME = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
SECOND_TIME = datetime(2026, 7, 24, 9, 30, tzinfo=UTC)


def override_db_session(
    session: AsyncSession,
) -> Callable[[], AsyncIterator[AsyncSession]]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield session

    return override


def make_knowledge_base(
    *,
    knowledge_base_id: UUID,
    name: str,
    description: str | None,
    created_at: datetime,
) -> KnowledgeBase:
    knowledge_base = KnowledgeBase(name=name, description=description)
    knowledge_base.id = knowledge_base_id
    knowledge_base.created_at = created_at
    knowledge_base.updated_at = created_at
    return knowledge_base


def test_list_knowledge_bases_returns_database_rows() -> None:
    session = AsyncMock(spec=AsyncSession)
    database_result = MagicMock()
    database_result.scalars.return_value.all.return_value = [
        make_knowledge_base(
            knowledge_base_id=FIRST_ID,
            name="Information security",
            description="Security policies",
            created_at=FIRST_TIME,
        ),
        make_knowledge_base(
            knowledge_base_id=SECOND_ID,
            name="Employee handbook",
            description=None,
            created_at=SECOND_TIME,
        ),
    ]
    session.execute.return_value = database_result
    app.dependency_overrides[get_db_session] = override_db_session(session)

    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/knowledge-bases")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": str(FIRST_ID),
            "name": "Information security",
            "description": "Security policies",
            "created_at": "2026-07-24T10:30:00Z",
            "updated_at": "2026-07-24T10:30:00Z",
        },
        {
            "id": str(SECOND_ID),
            "name": "Employee handbook",
            "description": None,
            "created_at": "2026-07-24T09:30:00Z",
            "updated_at": "2026-07-24T09:30:00Z",
        },
    ]
    session.execute.assert_awaited_once()


def test_get_knowledge_base_returns_database_row() -> None:
    session = AsyncMock(spec=AsyncSession)
    knowledge_base = make_knowledge_base(
        knowledge_base_id=FIRST_ID,
        name="Information security",
        description="Security policies",
        created_at=FIRST_TIME,
    )
    session.get.return_value = knowledge_base
    app.dependency_overrides[get_db_session] = override_db_session(session)

    try:
        with TestClient(app) as client:
            response = client.get(f"/api/v1/knowledge-bases/{FIRST_ID}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["id"] == str(FIRST_ID)
    assert response.json()["name"] == "Information security"
    session.get.assert_awaited_once_with(KnowledgeBase, FIRST_ID)


def test_get_knowledge_base_returns_not_found() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = None
    app.dependency_overrides[get_db_session] = override_db_session(session)

    try:
        with TestClient(app) as client:
            response = client.get(f"/api/v1/knowledge-bases/{FIRST_ID}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Knowledge base not found"}
    session.get.assert_awaited_once_with(KnowledgeBase, FIRST_ID)


def test_get_knowledge_base_rejects_invalid_uuid() -> None:
    session = AsyncMock(spec=AsyncSession)
    app.dependency_overrides[get_db_session] = override_db_session(session)

    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/knowledge-bases/not-a-uuid")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    session.get.assert_not_awaited()
