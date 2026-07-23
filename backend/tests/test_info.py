from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_info_check_returns_application_status() -> None:
    response = client.get("/api/v1/info")
    assert response.status_code == 200
    assert response.json() == {
        "project": "Enterprise RAG Assistant",
        "stage": 3,
        "features": ["health-check", "postgres-pgvector", "database-readiness"],
    }
