from uuid import UUID

from fastapi.testclient import TestClient

from app.main import app


def test_request_id_is_returned_and_trace_can_be_looked_up() -> None:
    request_id = "8f3b5d9b-9546-4c2a-a1ed-7ac3b8bb5fd5"
    with TestClient(app) as client:
        response = client.get("/api/v1/health", headers={"X-Request-ID": request_id})
        trace_response = client.get(f"/api/v1/observability/requests/{request_id}")

    assert response.status_code == 200
    assert UUID(response.headers["X-Request-ID"]) == UUID(request_id)
    assert trace_response.status_code == 200
    trace = trace_response.json()
    assert trace["request_id"] == request_id
    assert trace["status_code"] == 200
    assert any(event["stage"] == "request" for event in trace["events"])


def test_invalid_request_id_is_replaced_without_exposing_arbitrary_header() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health", headers={"X-Request-ID": "not-a-uuid"})

    assert response.status_code == 200
    assert UUID(response.headers["X-Request-ID"])
    assert response.headers["X-Request-ID"] != "not-a-uuid"


def test_metrics_endpoint_exposes_aggregate_stage_counts_only() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/observability/metrics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["requests"]["total"] >= 1
    assert "request" in payload["stages"]
    assert "question" not in str(payload)
