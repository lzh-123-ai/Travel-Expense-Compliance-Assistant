from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_check_returns_application_status() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "app_name": "Enterprise RAG Assistant",
        "environment": "development",
    }


def test_unknown_endpoint_returns_not_found() -> None:
    response = client.get("/api/v1/not-found")
    assert response.status_code == 404
