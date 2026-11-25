"""管理端 API 测试。"""

from __future__ import annotations

from typing import Any, Callable

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import session_scope, utcnow
from app.models import AuditLog, Incident


def _login(client: TestClient, password: str = "test-pass") -> Any:
    return client.post(
        "/api/v1/admin/login",
        json={"username": "admin", "password": password},
    )


def test_admin_endpoints_require_login(client: TestClient) -> None:
    assert client.get("/api/v1/admin/overview").status_code == 401
    assert client.get("/api/v1/admin/clients").status_code == 401
    assert client.get("/api/v1/admin/incidents").status_code == 401
    assert client.post("/api/v1/admin/incidents/1/ack").status_code == 401


def test_admin_login_rejects_wrong_password(client: TestClient) -> None:
    response = _login(client, password="wrong")
    assert response.status_code == 401
    assert "vigil_session" not in client.cookies


def test_admin_login_overview_and_audit(
    client: TestClient, registered: dict[str, Any]
) -> None:
    assert _login(client).status_code == 200
    assert "vigil_session" in client.cookies

    response = client.get("/api/v1/admin/overview")
    assert response.status_code == 200
    data = response.json()
    assert data["clients_total"] == 1
    assert data["clients_online"] == 0
    assert data["incidents_open"] == 0

    clients = client.get("/api/v1/admin/clients").json()["items"]
    assert [item["client_id"] for item in clients] == [registered["client_id"]]

    with session_scope() as db:
        actions = db.scalars(select(AuditLog.action)).all()
    assert "login" in actions


def test_admin_client_detail_and_ack(
    client: TestClient, registered: dict[str, Any], auth: Callable[..., dict[str, str]]
) -> None:
    heartbeat = client.post(
        "/api/v1/heartbeat",
        headers=auth(registered["client_id"], registered["secret"]),
        json={
            "client_id": registered["client_id"],
            "seq": 1,
            "boot_id": "boot-1",
            "uptime": 60,
            "agent_version": "0.1.0",
            "wan_ip": "203.0.113.7",
            "modules": {"net.carrier": {"status": "ok", "latency_ms": 0}},
        },
    )
    assert heartbeat.status_code == 200

    now = utcnow()
    with session_scope() as db:
        db.add(
            Incident(
                client_id=registered["client_id"],
                category="agent_down",
                state="DOWN",
                started_at=now,
                detected_at=now,
                summary="测试故障",
                evidence_json="{}",
            )
        )

    assert _login(client).status_code == 200

    detail = client.get(f"/api/v1/admin/clients/{registered['client_id']}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["client"]["client_id"] == registered["client_id"]
    assert len(payload["heartbeats"]) == 1
    assert len(payload["incidents"]) == 1

    incidents = client.get("/api/v1/admin/incidents").json()["items"]
    assert len(incidents) == 1
    incident_id = incidents[0]["id"]

    ack = client.post(f"/api/v1/admin/incidents/{incident_id}/ack")
    assert ack.status_code == 200
    assert ack.json()["acked_at"] is not None

    with session_scope() as db:
        actions = db.scalars(select(AuditLog.action)).all()
    assert "ack_incident" in actions


def test_admin_client_detail_missing(client: TestClient) -> None:
    assert _login(client).status_code == 200
    assert client.get("/api/v1/admin/clients/cl_unknown").status_code == 404
