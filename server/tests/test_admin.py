"""管理端 API 测试。"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

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
    assert client.get("/api/v1/admin/audit").status_code == 401
    assert client.get("/api/v1/admin/audit/export").status_code == 401
    assert client.get("/api/v1/admin/policies").status_code == 401
    assert client.put("/api/v1/admin/policies", json={}).status_code == 401


def test_admin_login_rejects_wrong_password(client: TestClient) -> None:
    response = _login(client, password="wrong")
    assert response.status_code == 401
    assert "vigil_session" not in client.cookies


def test_admin_login_rate_limited(client: TestClient) -> None:
    from app.api_admin import LOGIN_MAX_FAILURES

    for _ in range(LOGIN_MAX_FAILURES):
        assert _login(client, password="wrong").status_code == 401

    blocked = _login(client, password="wrong")
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"].isdigit()
    # 限流期间即使密码正确也不放行
    assert _login(client).status_code == 429


def test_admin_login_success_resets_limiter(client: TestClient) -> None:
    from app.api_admin import LOGIN_MAX_FAILURES

    for _ in range(LOGIN_MAX_FAILURES - 1):
        assert _login(client, password="wrong").status_code == 401
    assert _login(client).status_code == 200

    for _ in range(LOGIN_MAX_FAILURES - 1):
        assert _login(client, password="wrong").status_code == 401
    assert _login(client, password="wrong").status_code == 401


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


def test_admin_policies_update_and_audit(client: TestClient) -> None:
    assert _login(client).status_code == 200

    initial = client.get("/api/v1/admin/policies")
    assert initial.status_code == 200
    assert initial.json()["policies"]["flapping_threshold"] == 3

    updated = client.put(
        "/api/v1/admin/policies",
        json={"flapping_threshold": 4, "retention_days": 30},
    )
    assert updated.status_code == 200
    policies = updated.json()["policies"]
    assert policies["flapping_threshold"] == 4
    assert policies["retention_days"] == 30
    assert policies["down_seconds"] == 150

    assert client.put(
        "/api/v1/admin/policies", json={"flapping_threshold": 1}
    ).status_code == 400

    with session_scope() as db:
        rows = db.scalars(
            select(AuditLog).where(AuditLog.action == "update_policies")
        ).all()
        assert len(rows) == 1


def test_admin_policies_apply_to_client_config(
    client: TestClient, registered: dict[str, Any], auth: Callable[..., dict[str, str]]
) -> None:
    assert _login(client).status_code == 200
    response = client.put(
        "/api/v1/admin/policies",
        json={
            "heartbeat_interval": 45,
            "action_cooldown_seconds": 120,
            "action_max_per_hour": 5,
        },
    )
    assert response.status_code == 200

    config = client.get(
        "/api/v1/config", headers=auth(registered["client_id"], registered["secret"])
    )
    assert config.status_code == 200
    policy = config.json()
    assert policy["heartbeat_interval"] == 45
    assert policy["action"] == {
        "max_level": "L1",
        "cooldown_seconds": 120,
        "max_per_hour": 5,
    }


def test_admin_login_failure_and_rate_limit_are_audited(client: TestClient) -> None:
    from app.api_admin import LOGIN_MAX_FAILURES

    for _ in range(LOGIN_MAX_FAILURES):
        assert _login(client, password="wrong").status_code == 401
    assert _login(client).status_code == 429

    with session_scope() as db:
        actions = db.scalars(
            select(AuditLog.action)
            .where(AuditLog.action.in_(("login_fail", "login_blocked")))
            .order_by(AuditLog.id)
        ).all()
    assert actions.count("login_fail") == LOGIN_MAX_FAILURES
    assert actions.count("login_blocked") == 1


def test_admin_audit_list_filter_and_export(
    client: TestClient, registered: dict[str, Any]
) -> None:
    assert _login(client).status_code == 200
    # 清除守护项配置会产生一条 guards_clear 审计
    assert client.delete(
        f"/api/v1/admin/clients/{registered['client_id']}/guards"
    ).status_code == 200

    data = client.get("/api/v1/admin/audit").json()
    actions = [item["action"] for item in data["items"]]
    assert data["total"] >= 2
    assert "login" in actions
    assert "guards_clear" in actions
    assert data["items"][0]["ts"].endswith("Z")

    filtered = client.get("/api/v1/admin/audit", params={"action": "login"}).json()
    assert filtered["total"] == 1
    assert [item["action"] for item in filtered["items"]] == ["login"]

    export = client.get("/api/v1/admin/audit/export", params={"action": "login"})
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("application/x-ndjson")
    lines = [line for line in export.text.splitlines() if line.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["action"] == "login"


def test_admin_client_detail_missing(client: TestClient) -> None:
    assert _login(client).status_code == 200
    assert client.get("/api/v1/admin/clients/cl_unknown").status_code == 404
