"""客户端取消注册与管理端删除客户端测试。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import session_scope, utcnow
from app.models import (
    Beacon,
    Client,
    ClientEvent,
    Heartbeat,
    Incident,
    Notification,
)


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/v1/admin/login", json={"username": "admin", "password": "test-pass"}
        ).status_code
        == 200
    )


def _heartbeat(
    client: TestClient,
    auth: Callable[..., dict[str, str]],
    registered: dict[str, Any],
) -> Any:
    return client.post(
        "/api/v1/heartbeat",
        headers=auth(registered["client_id"], registered["secret"]),
        json={
            "client_id": registered["client_id"],
            "seq": 1,
            "boot_id": "boot-1",
            "uptime": 10,
            "agent_version": "0.1.0",
            "checks": {},
            "probes": [],
            "spool": {"pending": 0, "dropped": 0},
        },
    )


def test_client_unregister_marks_revoked(
    client: TestClient,
    registered: dict[str, Any],
    auth: Callable[..., dict[str, str]],
) -> None:
    client_id = registered["client_id"]
    headers = auth(client_id, registered["secret"])

    response = client.post(
        "/api/v1/unregister", headers=headers, json={"reason": "manual_cancel"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["revoked_at"].endswith("Z")

    # 失效后原凭据不能再访问任何客户端接口
    assert _heartbeat(client, auth, registered).status_code == 403

    _login(client)
    items = client.get("/api/v1/admin/clients").json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["enabled"] is False
    assert items[0]["lifecycle"] == "REVOKED"
    assert items[0]["state"] == "UNKNOWN"
    assert items[0]["revoked_reason"] == "manual_cancel"

    overview = client.get("/api/v1/admin/overview").json()["data"]
    assert overview["clients_total"] == 0
    assert overview["clients_online"] == 0


def test_admin_delete_client_purges_related_data(
    client: TestClient,
    registered: dict[str, Any],
    auth: Callable[..., dict[str, str]],
) -> None:
    client_id = registered["client_id"]
    assert _heartbeat(client, auth, registered).status_code == 200
    assert (
        client.post(
            "/api/v1/beacon",
            headers=auth(client_id, registered["secret"]),
            json={"client_id": client_id, "seq": 1, "boot_id": "boot-1", "uptime": 10},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/events",
            headers=auth(client_id, registered["secret"]),
            json={
                "client_id": client_id,
                "events": [
                    {
                        "event_id": "evt-1",
                        "seq": 1,
                        "type": "test",
                        "domain": "agent",
                        "payload": {},
                    }
                ],
            },
        ).status_code
        == 200
    )
    now = utcnow()
    with session_scope() as db:
        db.add(
            Incident(
                client_id=client_id,
                category="AGENT_DOWN",
                state="DOWN",
                started_at=now,
                detected_at=now,
                summary="test",
                evidence_json="{}",
            )
        )
        db.add(
            Notification(
                client_id=client_id,
                channel="feishu",
                kind="down",
                status="pending",
            )
        )

    _login(client)
    deleted = client.delete(f"/api/v1/admin/clients/{client_id}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["data"] == {"client_id": client_id, "deleted": True}

    with session_scope() as db:
        assert db.scalar(select(Client).where(Client.client_id == client_id)) is None
        for model in (Beacon, Heartbeat, ClientEvent, Incident, Notification):
            count = db.scalar(
                select(func.count())
                .select_from(model)
                .where(model.client_id == client_id)
            )
            assert count == 0, model.__name__
    assert client.delete(f"/api/v1/admin/clients/{client_id}").status_code == 404
    assert _heartbeat(client, auth, registered).status_code == 401

    audit = client.get("/api/v1/admin/audit", params={"action": "client.delete"}).json()
    assert audit["data"]["total"] == 1


def test_admin_delete_requires_login(
    client: TestClient, registered: dict[str, Any]
) -> None:
    assert client.delete(f"/api/v1/admin/clients/{registered['client_id']}").status_code == 401
