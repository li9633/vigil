"""管理端 API 测试。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import pytest
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
    assert client.get("/api/v1/admin/system").status_code == 401
    assert client.put("/api/v1/admin/system/admin-password", json={}).status_code == 401
    assert client.post("/api/v1/admin/clients/cl_x/secret/rotate").status_code == 401


def test_admin_login_rejects_wrong_password(client: TestClient) -> None:
    response = _login(client, password="wrong")
    assert response.status_code == 401
    assert "vigil_session" not in client.cookies


def test_admin_login_rate_limited(client: TestClient) -> None:
    from app.services.auth_service import LOGIN_MAX_FAILURES

    for _ in range(LOGIN_MAX_FAILURES):
        assert _login(client, password="wrong").status_code == 401

    blocked = _login(client, password="wrong")
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"].isdigit()
    # 限流期间即使密码正确也不放行
    assert _login(client).status_code == 429


def test_admin_login_success_resets_limiter(client: TestClient) -> None:
    from app.services.auth_service import LOGIN_MAX_FAILURES

    for _ in range(LOGIN_MAX_FAILURES - 1):
        assert _login(client, password="wrong").status_code == 401
    assert _login(client).status_code == 200

    for _ in range(LOGIN_MAX_FAILURES - 1):
        assert _login(client, password="wrong").status_code == 401
    assert _login(client, password="wrong").status_code == 401


def test_admin_login_returns_envelope(client: TestClient) -> None:
    body = _login(client).json()
    assert body["code"] == "ok"
    assert body["message"] == "ok"
    assert body["data"]["actor"] == "admin"
    assert isinstance(body["data"]["server_time"], int)


def test_admin_login_error_envelope(client: TestClient) -> None:
    body = _login(client, password="wrong").json()
    assert body["code"] == "unauthorized"
    assert body["message"] == "用户名或密码错误"
    assert body["data"] is None


def test_admin_login_overview_and_audit(
    client: TestClient, registered: dict[str, Any]
) -> None:
    assert _login(client).status_code == 200
    assert "vigil_session" in client.cookies

    response = client.get("/api/v1/admin/overview")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["clients_total"] == 1
    assert data["clients_online"] == 0
    assert data["incidents_open"] == 0

    clients = client.get("/api/v1/admin/clients").json()["data"]["items"]
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
            "checks": {"wan-link": {"check_id": "wan-link", "status": "ok", "latency_ms": 0}},
            "probes": [{"id": "carrier", "name": "物理链路"}],
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
    payload = detail.json()["data"]
    assert payload["client"]["client_id"] == registered["client_id"]
    assert len(payload["heartbeats"]) == 1
    assert len(payload["incidents"]) == 1

    incidents = client.get("/api/v1/admin/incidents").json()["data"]["items"]
    assert len(incidents) == 1
    incident_id = incidents[0]["id"]

    ack = client.post(f"/api/v1/admin/incidents/{incident_id}/ack")
    assert ack.status_code == 200
    assert ack.json()["data"]["acked_at"] is not None

    with session_scope() as db:
        actions = db.scalars(select(AuditLog.action)).all()
    assert "ack_incident" in actions


def test_admin_policies_update_and_audit(client: TestClient) -> None:
    assert _login(client).status_code == 200

    initial = client.get("/api/v1/admin/policies")
    assert initial.status_code == 200
    assert initial.json()["data"]["policies"]["flapping_threshold"] == 3

    updated = client.put(
        "/api/v1/admin/policies",
        json={"flapping_threshold": 4, "retention_days": 30},
    )
    assert updated.status_code == 200
    policies = updated.json()["data"]["policies"]
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
    policy = config.json()["data"]
    assert policy["heartbeat_interval"] == 45
    assert policy["action"] == {
        "max_level": "L1",
        "cooldown_seconds": 120,
        "max_per_hour": 5,
    }


def test_admin_login_failure_and_rate_limit_are_audited(client: TestClient) -> None:
    from app.services.auth_service import LOGIN_MAX_FAILURES

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

    data = client.get("/api/v1/admin/audit").json()["data"]
    actions = [item["action"] for item in data["items"]]
    assert data["total"] >= 1
    assert "login" in actions
    assert data["items"][0]["ts"].endswith("Z")

    filtered = client.get("/api/v1/admin/audit", params={"action": "login"}).json()["data"]
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


def test_admin_stream_requires_login(client: TestClient) -> None:
    assert client.get("/api/v1/admin/stream").status_code == 401
    assert client.get("/api/v1/admin/stream", params={"once": 1}).status_code == 401


def test_admin_stream_once_returns_ready_frame(client: TestClient) -> None:
    assert _login(client).status_code == 200
    response = client.get("/api/v1/admin/stream", params={"once": 1})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    text = response.text
    assert "retry: " in text
    assert "event: ready" in text
    ready = json.loads(text.split("data: ", 1)[1].split("\n\n", 1)[0])
    assert set(ready["version"]) == {"incidents", "clients", "guards", "notifications"}


def test_stream_snapshot_tracks_incident_changes(client: TestClient) -> None:
    from app.services.stream_service import snapshot

    before = snapshot()
    with session_scope() as db:
        incident = Incident(
            client_id="demo-client",
            category="agent_down",
            state="DOWN",
            started_at=utcnow(),
            detected_at=utcnow(),
            summary="测试推送",
        )
        db.add(incident)
        db.flush()
        incident_id = incident.id

    after_insert = snapshot()
    assert after_insert["incidents"][0] == before["incidents"][0] + 1
    assert after_insert["incidents"][1] == incident_id

    with session_scope() as db:
        row = db.get(Incident, incident_id)
        assert row is not None
        row.acked_at = utcnow()

    after_ack = snapshot()
    assert after_ack["incidents"] != after_insert["incidents"]


class _FakeRequest:
    """只实现 is_disconnected，便于直接驱动 SSE 生成器。"""

    def __init__(self, disconnect_after: int) -> None:
        self.calls = 0
        self.disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self.calls += 1
        return self.calls > self.disconnect_after


def test_admin_event_stream_emits_update_and_stops(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.admin import stream as stream_module

    monkeypatch.setattr(stream_module, "STREAM_INTERVAL_SECONDS", 0)

    async def run() -> tuple[list[str], str]:
        request = _FakeRequest(disconnect_after=1)
        stream = stream_module._admin_event_stream(request, once=False)
        first = await stream.__anext__()
        ready = await stream.__anext__()

        with session_scope() as db:
            db.add(
                Incident(
                    client_id="demo-client",
                    category="agent_down",
                    state="DOWN",
                    started_at=utcnow(),
                    detected_at=utcnow(),
                    summary="推送测试",
                )
            )

        update = await stream.__anext__()
        with pytest.raises(StopAsyncIteration):
            await stream.__anext__()
        return [first, ready, update], update

    frames, update = asyncio.run(run())
    assert frames[0].startswith("retry: ")
    assert "event: ready" in frames[1]
    assert "event: update" in update
    assert '"changed": ["incidents"]' in update


def test_admin_system_settings(client: TestClient) -> None:
    assert _login(client).status_code == 200
    data = client.get("/api/v1/admin/system").json()["data"]
    assert data["admin_username"] == "admin"
    assert data["password_updated_at"]
    assert data["retention_days"] >= 0
    assert 50 <= data["disk_alert_percent"] <= 99


def test_admin_password_change_persists_to_database(client: TestClient) -> None:
    assert _login(client).status_code == 200

    wrong = client.put(
        "/api/v1/admin/system/admin-password",
        json={"current_password": "bad", "new_password": "new-pass-123"},
    )
    assert wrong.status_code == 400

    short = client.put(
        "/api/v1/admin/system/admin-password",
        json={"current_password": "test-pass", "new_password": "short"},
    )
    assert short.status_code == 400

    changed = client.put(
        "/api/v1/admin/system/admin-password",
        json={"current_password": "test-pass", "new_password": "new-pass-123"},
    )
    assert changed.status_code == 200
    assert changed.json()["data"]["password_updated_at"]

    system = client.get("/api/v1/admin/system").json()["data"]
    assert "password_source" not in system

    # 修改后旧密码失效，新密码可登录
    assert _login(client, password="test-pass").status_code == 401
    assert _login(client, password="new-pass-123").status_code == 200

    audit = client.get(
        "/api/v1/admin/audit", params={"action": "update_admin_password"}
    ).json()["data"]
    assert audit["total"] == 1


def test_rotate_client_secret(
    client: TestClient,
    registered: dict[str, Any],
    auth: Callable[..., dict[str, str]],
) -> None:
    assert _login(client).status_code == 200
    client_id = registered["client_id"]
    old_secret = registered["secret"]

    rotated = client.post(f"/api/v1/admin/clients/{client_id}/secret/rotate")
    assert rotated.status_code == 200
    data = rotated.json()["data"]
    new_secret = data["secret"]
    assert new_secret and new_secret != old_secret
    assert data["rotated_at"].endswith("Z")

    def beacon(secret: str) -> Any:
        return client.post(
            "/api/v1/beacon",
            headers=auth(client_id, secret),
            json={"client_id": client_id, "seq": 1, "boot_id": "boot-1", "uptime": 10},
        )

    assert beacon(old_secret).status_code == 401
    assert beacon(new_secret).status_code == 200

    assert client.post("/api/v1/admin/clients/cl_missing/secret/rotate").status_code == 404
    audit = client.get(
        "/api/v1/admin/audit", params={"action": "rotate_client_secret"}
    ).json()["data"]
    assert audit["total"] == 1
