from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.main import app


async def override_working_db_session() -> AsyncIterator[AsyncSession]:
    session = AsyncMock(spec=AsyncSession)
    yield session


def test_readiness_check_returns_ready_when_database_responds() -> None:
    app.dependency_overrides[get_db_session] = override_working_db_session
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/ready")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok"}


async def override_failing_db_session() -> AsyncIterator[AsyncSession]:
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = SQLAlchemyError("database unavailable")
    yield session


def test_readiness_check_returns_unavailable_when_database_fails() -> None:
    app.dependency_overrides[get_db_session] = override_failing_db_session
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/ready")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503
    assert response.json() == {"detail": "Database is unavailable"}
