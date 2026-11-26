"""客户端接入协议测试。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import session_scope
from app.models import ClientEvent, Heartbeat

BOOTSTRAP_TOKEN = "test-bootstrap"


def _heartbeat_body(client_id: str) -> dict[str, Any]:
    return {
        "client_id": client_id,
        "seq": 1,
        "boot_id": "boot-1",
        "uptime": 120,
        "agent_version": "0.1.0",
        "wan_ip": "203.0.113.7",
        "checks": {"wan-link": {"check_id": "wan-link", "status": "ok", "latency_ms": 0}},
        "probes": [{"id": "carrier", "name": "物理链路", "remote_allowed": True}],
        "spool": {"pending": 0, "dropped": 0},
    }


def test_register_success(client: TestClient) -> None:
    response = client.post(
        "/api/v1/register",
        headers={"X-Vigil-Bootstrap": BOOTSTRAP_TOKEN},
        json={
            "name": "router-a",
            "model": "iStoreOS",
            "fw_version": "24.10",
            "agent_version": "0.1.0",
            "protocol": 1,
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["client_id"].startswith("cl_")
    assert len(data["secret"]) >= 32
    assert data["heartbeat_interval"] == 30
    assert isinstance(data["server_time"], int)


def test_register_rejects_bad_bootstrap(client: TestClient) -> None:
    body = {
        "name": "router-a",
        "model": "iStoreOS",
        "fw_version": "24.10",
        "agent_version": "0.1.0",
        "protocol": 1,
    }
    wrong = client.post(
        "/api/v1/register", headers={"X-Vigil-Bootstrap": "wrong"}, json=body
    )
    assert wrong.status_code == 401

    missing = client.post("/api/v1/register", json=body)
    assert missing.status_code == 401


def test_auth_rejections(
    client: TestClient, registered: dict[str, Any], auth: Callable[..., dict[str, str]]
) -> None:
    body = _heartbeat_body(registered["client_id"])

    assert client.post("/api/v1/heartbeat", json=body).status_code == 401

    wrong_secret = client.post(
        "/api/v1/heartbeat",
        headers=auth(registered["client_id"], "not-the-secret"),
        json=body,
    )
    assert wrong_secret.status_code == 401

    stale = client.post(
        "/api/v1/heartbeat",
        headers=auth(registered["client_id"], registered["secret"], timestamp=1),
        json=body,
    )
    assert stale.status_code == 401

    nonce = "fixed-nonce-for-replay"
    first = client.post(
        "/api/v1/heartbeat",
        headers=auth(registered["client_id"], registered["secret"], nonce=nonce),
        json=body,
    )
    assert first.status_code == 200
    replay = client.post(
        "/api/v1/heartbeat",
        headers=auth(registered["client_id"], registered["secret"], nonce=nonce),
        json=body,
    )
    assert replay.status_code == 401


def test_heartbeat_ok(
    client: TestClient, registered: dict[str, Any], auth: Callable[..., dict[str, str]]
) -> None:
    response = client.post(
        "/api/v1/heartbeat",
        headers=auth(registered["client_id"], registered["secret"]),
        json=_heartbeat_body(registered["client_id"]),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ok"] is True
    assert data["actions_enabled"] is True
    assert len(data["config_etag"]) == 32

    with session_scope() as db:
        count = db.scalar(select(func.count()).select_from(Heartbeat))
    assert count == 1


def test_beacon_ok(
    client: TestClient, registered: dict[str, Any], auth: Callable[..., dict[str, str]]
) -> None:
    response = client.post(
        "/api/v1/beacon",
        headers=auth(registered["client_id"], registered["secret"]),
        json={
            "client_id": registered["client_id"],
            "seq": 1,
            "boot_id": "boot-1",
            "uptime": 100,
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ok"] is True
    assert isinstance(data["server_time"], int)


def test_events_idempotent(
    client: TestClient, registered: dict[str, Any], auth: Callable[..., dict[str, str]]
) -> None:
    payload = {
        "client_id": registered["client_id"],
        "events": [
            {
                "event_id": "evt-1",
                "seq": 1,
                "type": "module_result",
                "domain": "net.dns",
                "payload": {"status": "warn"},
            }
        ],
    }
    first = client.post(
        "/api/v1/events",
        headers=auth(registered["client_id"], registered["secret"]),
        json=payload,
    )
    assert first.status_code == 200
    assert first.json()["data"]["acked"] == ["evt-1"]

    second = client.post(
        "/api/v1/events",
        headers=auth(registered["client_id"], registered["secret"]),
        json=payload,
    )
    assert second.status_code == 200
    assert second.json()["data"]["acked"] == ["evt-1"]

    with session_scope() as db:
        count = db.scalar(select(func.count()).select_from(ClientEvent))
    assert count == 1


def test_events_batch_size_limited(
    client: TestClient, registered: dict[str, Any], auth: Callable[..., dict[str, str]]
) -> None:
    events = [
        {"event_id": f"evt-{index}", "seq": index, "type": "module_result", "domain": "net.dns"}
        for index in range(201)
    ]
    response = client.post(
        "/api/v1/events",
        headers=auth(registered["client_id"], registered["secret"]),
        json={"client_id": registered["client_id"], "events": events},
    )
    assert response.status_code == 422


def test_config_etag(
    client: TestClient, registered: dict[str, Any], auth: Callable[..., dict[str, str]]
) -> None:
    headers = auth(registered["client_id"], registered["secret"])
    response = client.get("/api/v1/config", headers=headers)
    assert response.status_code == 200
    etag = response.headers["ETag"]
    body = response.json()["data"]
    assert body["schema_version"] == 3
    assert body["heartbeat_interval"] == 30
    assert body["actions_enabled"] is True
    assert body["thresholds"]["suspect_seconds"] == 90

    cached = client.get(
        "/api/v1/config",
        headers={
            **auth(registered["client_id"], registered["secret"]),
            "If-None-Match": etag,
        },
    )
    assert cached.status_code == 304
