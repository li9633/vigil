"""端到端集成：注册 → 心跳 → beacon → 管理端登录 → 总览 / 客户端 / 告警。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from app.db import session_scope, utcnow
from app.models import Incident

BOOTSTRAP_TOKEN = "test-bootstrap"


def test_full_client_and_admin_flow(
    client: TestClient, auth: Callable[..., dict[str, str]]
) -> None:
    registered = client.post(
        "/api/v1/register",
        headers={"X-Vigil-Bootstrap": BOOTSTRAP_TOKEN},
        json={
            "name": "router-flow",
            "model": "iStoreOS",
            "fw_version": "24.10",
            "agent_version": "0.1.0",
            "protocol": 1,
        },
    )
    assert registered.status_code == 200, registered.text
    credentials: dict[str, Any] = registered.json()
    client_id = credentials["client_id"]
    secret = credentials["secret"]

    heartbeat = client.post(
        "/api/v1/heartbeat",
        headers=auth(client_id, secret),
        json={
            "client_id": client_id,
            "seq": 1,
            "boot_id": "boot-flow",
            "uptime": 3600,
            "agent_version": "0.1.0",
            "wan_ip": "203.0.113.9",
            "modules": {
                "net.carrier": {"status": "ok", "latency_ms": 0},
                "net.pppoe": {"status": "ok", "uptime": 1200},
            },
            "spool": {"pending": 0, "dropped": 0},
        },
    )
    assert heartbeat.status_code == 200, heartbeat.text

    beacon = client.post(
        "/api/v1/beacon",
        headers=auth(client_id, secret),
        json={"client_id": client_id, "seq": 1, "boot_id": "boot-flow", "uptime": 3600},
    )
    assert beacon.status_code == 200, beacon.text

    now = utcnow()
    with session_scope() as db:
        db.add(
            Incident(
                client_id=client_id,
                category="agent_down",
                state="DOWN",
                started_at=now,
                detected_at=now,
                summary="心跳缺失",
                evidence_json="{}",
            )
        )

    login = client.post(
        "/api/v1/admin/login", json={"username": "admin", "password": "test-pass"}
    )
    assert login.status_code == 200

    overview = client.get("/api/v1/admin/overview").json()
    assert overview["clients_total"] == 1
    assert overview["clients_online"] == 1
    assert overview["clients_down"] == 0
    assert overview["incidents_open"] == 1
    assert overview["open_incidents"] == overview["incidents_open"]
    assert overview["incidents_24h"] == 1
    assert overview["flapping_clients"] == 0
    assert overview["notification_success_rate"] is None
    assert overview["generated_at"]
    assert len(overview["timeline"]) == 1
    timeline_item = overview["timeline"][0]
    assert set(["id", "ts", "type", "severity", "client_id", "summary"]).issubset(
        timeline_item
    )
    assert timeline_item["client_id"] == client_id
    assert timeline_item["type"] == "agent_down"
    assert timeline_item["severity"] == "DOWN"

    clients = client.get("/api/v1/admin/clients").json()["items"]
    assert len(clients) == 1
    listed = clients[0]
    assert listed["client_id"] == client_id
    assert listed["state"] == "HEALTHY"
    assert listed["last_incident"]["client_id"] == client_id

    detail = client.get(f"/api/v1/admin/clients/{client_id}").json()
    assert detail["client"]["client_id"] == client_id
    assert [module["id"] for module in detail["modules"]] == [
        "net.carrier",
        "net.pppoe",
    ]
    assert detail["heartbeats"][0]["ts"] == detail["heartbeats"][0]["received_at"]
    assert detail["recent_events"] == []
    assert detail["actions"] == []
    assert detail["generated_at"]

    incidents = client.get("/api/v1/admin/incidents").json()["items"]
    assert len(incidents) == 1
    assert incidents[0]["state"] == "DOWN"
    assert incidents[0]["client_id"] == client_id
