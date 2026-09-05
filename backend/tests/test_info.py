from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app

client = TestClient(app)


def test_info_check_returns_application_status() -> None:
    response = client.get("/api/v1/info")
    assert response.status_code == 200
    assert response.json() == {
        "project": "Enterprise RAG Assistant",
        "stage": 16,
        "features": [
            "document-ingestion",
            "hybrid-retrieval",
            "grounded-answering",
            "tool-routing",
            "observability",
        ],
        "tool_routing_provider": get_settings().tool_routing_provider,
    }
