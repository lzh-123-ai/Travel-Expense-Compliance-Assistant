from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# 测试函数 1：检查健康接口
def test_health_check_returns_application_status() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "app_name": "Enterprise RAG Assistant",
        "environment": "development",
    }


# 测试函数2：检查错误处理
def test_unknown_endpoint_returns_not_found() -> None:
    response = client.get("/api/v1/not-found")
    assert response.status_code == 404
