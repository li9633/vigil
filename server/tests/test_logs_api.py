"""服务端日志 API 测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/v1/admin/login",
        json={"username": "admin", "password": "test-pass"},
    )
    assert response.status_code == 200, response.text


def test_logs_require_login(client: TestClient) -> None:
    assert client.get("/api/v1/admin/logs").status_code == 401
    assert client.get("/api/v1/admin/logs/export").status_code == 401


def test_login_is_logged(client: TestClient) -> None:
    _login(client)
    response = client.get("/api/v1/admin/logs", params={"event": "auth.", "limit": 20})
    assert response.status_code == 200
    items = response.json()
    assert items
    success = [item for item in items if item["event"] == "auth.login.success"]
    assert success
    assert success[0]["origin"] == "server"
    assert success[0]["instance"] == "api"
    assert success[0]["request_id"]
    assert success[0]["actor"] == "admin"


def test_register_is_logged(client: TestClient, registered: dict) -> None:
    _login(client)
    response = client.get("/api/v1/admin/logs", params={"event": "client.register"})
    assert response.status_code == 200
    items = response.json()
    assert items
    assert items[-1]["client_id"] == registered["client_id"]


def test_logs_export_and_filters(client: TestClient) -> None:
    _login(client)
    response = client.get(
        "/api/v1/admin/logs/export",
        params={"event": "auth.", "instance": "api"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert "attachment" in response.headers["content-disposition"]
    assert b"auth.login.success" in response.content

    rejected = client.get("/api/v1/admin/logs", params={"instance": "other"})
    assert rejected.status_code == 400
