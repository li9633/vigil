"""配对流程测试：安全约束、领取凭证与过期清理。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import session_scope, utcnow
from app.models import PairingRequest

PAIRING_CODE = "ABCD-1234"


def _create(
    client: TestClient,
    *,
    instance_id: str = "instance-1",
    code: str = PAIRING_CODE,
    name: str = "router-pair",
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/pairing/requests",
        json={
            "instance_id": instance_id,
            "name": name,
            "model": "iStoreOS",
            "fw_version": "24.10",
            "agent_version": "0.1.0",
            "code": code,
            "protocol": 1,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _status(client: TestClient, request_id: str, claim_token: str) -> Any:
    return client.get(
        "/api/v1/pairing/requests/%s" % request_id,
        headers={"Authorization": "Pair %s" % claim_token},
    )


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/v1/admin/login", json={"username": "admin", "password": "test-pass"}
    )
    assert response.status_code == 200, response.text


def _approve(client: TestClient, request_id: str, code: str = PAIRING_CODE) -> Any:
    return client.post(
        "/api/v1/admin/pairing/%s/approve" % request_id, json={"code": code}
    )


def test_pairing_full_flow(
    client: TestClient, auth: Callable[..., dict[str, str]]
) -> None:
    created = _create(client)
    request_id = created["request_id"]
    claim_token = created["claim_token"]
    assert created["expires_at"] > created["server_time"]
    assert created["code_expires_at"] > created["server_time"]

    assert _status(client, request_id, "wrong-token").status_code == 401
    pending = _status(client, request_id, claim_token)
    assert pending.status_code == 200
    assert pending.json()["data"]["status"] == "pending"

    _login(client)
    listing = client.get("/api/v1/admin/pairing", params={"status": "pending"})
    assert listing.status_code == 200
    items = listing.json()["data"]["items"]
    assert [item["request_id"] for item in items] == [request_id]
    assert "code_hmac" not in items[0] and "claim_sha256" not in items[0]

    # 小写、带空格也能通过规范化校验
    wrong = _approve(client, request_id, "ZZZZ-ZZZZ")
    assert wrong.status_code == 400
    approved = _approve(client, request_id, "abcd 1234")
    assert approved.status_code == 200, approved.text
    assert approved.json()["data"]["status"] == "approved"

    claimed = _status(client, request_id, claim_token)
    assert claimed.status_code == 200
    body = claimed.json()["data"]
    assert body["status"] == "approved"
    assert body["client_id"].startswith("cl_")
    assert len(body["secret"]) >= 32

    # 凭证可重复领取，避免网络抖动导致客户端卡死
    again = _status(client, request_id, claim_token)
    assert again.json()["data"]["secret"] == body["secret"]

    heartbeat = client.post(
        "/api/v1/heartbeat",
        headers=auth(body["client_id"], body["secret"]),
        json={
            "client_id": body["client_id"],
            "seq": 1,
            "boot_id": "boot-1",
            "uptime": 10,
            "agent_version": "0.1.0",
            "checks": {},
            "probes": [],
            "spool": {"pending": 0, "dropped": 0},
        },
    )
    assert heartbeat.status_code == 200, heartbeat.text

    listing = client.get("/api/v1/admin/pairing")
    assert listing.json()["data"]["items"][0]["status"] == "claimed"


def test_pairing_denied_flow(client: TestClient) -> None:
    created = _create(client)
    _login(client)
    denied = client.post(
        "/api/v1/admin/pairing/%s/deny" % created["request_id"],
        json={"reason": "来源不可信"},
    )
    assert denied.status_code == 200
    assert denied.json()["data"]["status"] == "denied"

    body = _status(client, created["request_id"], created["claim_token"]).json()["data"]
    assert body["status"] == "denied"
    assert body["reason"] == "来源不可信"


def test_pairing_code_lockout(client: TestClient) -> None:
    created = _create(client)
    _login(client)
    for _ in range(4):
        assert _approve(client, created["request_id"], "ZZZZ-ZZZZ").status_code == 400
    assert _approve(client, created["request_id"], "ZZZZ-ZZZZ").status_code == 409
    # 已锁定后正确配对码也不再放行
    assert _approve(client, created["request_id"]).status_code == 409


def test_pairing_request_expires(client: TestClient) -> None:
    created = _create(client)
    with session_scope() as db:
        row = db.scalar(select(PairingRequest))
        row.expires_at = utcnow() - timedelta(seconds=1)

    body = _status(client, created["request_id"], created["claim_token"]).json()["data"]
    assert body["status"] == "expired"

    _login(client)
    assert _approve(client, created["request_id"]).status_code == 409


def test_pairing_rate_limit(client: TestClient) -> None:
    for index in range(5):
        _create(client, instance_id="instance-%d" % index)
    blocked = client.post(
        "/api/v1/pairing/requests",
        json={
            "instance_id": "instance-overflow",
            "name": "router-pair",
            "code": PAIRING_CODE,
            "protocol": 1,
        },
    )
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"].isdigit()


def test_pairing_admin_requires_login(client: TestClient) -> None:
    assert client.get("/api/v1/admin/pairing").status_code == 401
    assert client.post("/api/v1/admin/pairing/x/approve", json={"code": "x"}).status_code == 401
    assert client.post("/api/v1/admin/pairing/x/deny", json={}).status_code == 401
