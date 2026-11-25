"""服务端守护项配置（Guard）管理端 CRUD 与 /api/v1/config 下发测试。"""

from __future__ import annotations

import json
from typing import Any, Callable

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import session_scope
from app.models import AuditLog, GuardConfig

# 两个 UUID，B 的字典序小于 A，用于验证按 guard_id 排序
GUARD_A_ID = "8f14e45f-ceea-467f-a8d3-2b1f0c9d7e61"
GUARD_B_ID = "0f1f5a3c-3a2f-4b0d-9c1e-2d3a4b5c6d7e"


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/v1/admin/login",
        json={"username": "admin", "password": "test-pass"},
    )
    assert response.status_code == 200, response.text


def _guard(guard_id: str, name: str = "断网自动恢复") -> dict[str, Any]:
    """符合设计文档 3.8 的完整 Guard 配置。"""
    return {
        "id": guard_id,
        "name": name,
        "template": "network.up",
        "enabled": True,
        "checks": [
            {"probe": "carrier", "params": {"device": "auto"}, "role": "root"},
            {"probe": "dns", "params": {"name": "www.baidu.com"}},
        ],
        "policy": {
            "interval": 30,
            "fail_threshold": 2,
            "success_threshold": 1,
            "cooldown": 300,
            "max_actions_per_hour": 3,
            "gave_up_after": 3,
        },
        "recovery": [{"action": "ifup_wan", "verify_after": 20}],
        "limits": {"max_risk_level": "L1"},
        "evaluate": {"mode": "all"},
    }


def _guards_url(client_id: str) -> str:
    return f"/api/v1/admin/clients/{client_id}/guards"


def _get_config(
    client: TestClient,
    auth: Callable[..., dict[str, str]],
    registered: dict[str, Any],
    etag: str | None = None,
) -> Any:
    headers = auth(registered["client_id"], registered["secret"])
    if etag is not None:
        headers["If-None-Match"] = etag
    return client.get("/api/v1/config", headers=headers)


def test_guards_require_login(client: TestClient, registered: dict[str, Any]) -> None:
    client_id = registered["client_id"]
    assert client.get(_guards_url(client_id)).status_code == 401
    assert (
        client.put(_guards_url(client_id), json={"guards": []}).status_code == 401
    )
    assert client.delete(_guards_url(client_id)).status_code == 401


def test_guards_crud_and_delivery(
    client: TestClient,
    registered: dict[str, Any],
    auth: Callable[..., dict[str, str]],
) -> None:
    client_id = registered["client_id"]
    _login(client)

    # 初始：无服务端配置，config 响应保持旧结构
    initial = client.get(_guards_url(client_id))
    assert initial.status_code == 200
    assert initial.json() == {"items": [], "managed": False}

    legacy = _get_config(client, auth, registered)
    assert legacy.status_code == 200
    legacy_etag = legacy.headers["ETag"]
    assert "guards" not in legacy.json()

    # 整体写入（顺序故意打乱），响应按 guard_id 排序
    payload = {"guards": [_guard(GUARD_A_ID), _guard(GUARD_B_ID, "DNS 异常恢复")]}
    saved = client.put(_guards_url(client_id), json=payload)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["managed"] is True
    assert [item["id"] for item in body["items"]] == [GUARD_B_ID, GUARD_A_ID]
    assert body["items"][1]["checks"][0]["probe"] == "carrier"
    assert body["items"][1]["recovery"][0]["action"] == "ifup_wan"
    assert "updated_at" not in body["items"][0]

    # 持久化到 guards 表，元数据列齐全
    with session_scope() as db:
        rows = db.scalars(
            select(GuardConfig)
            .where(GuardConfig.client_id == client_id)
            .order_by(GuardConfig.guard_id)
        ).all()
    assert [row.guard_id for row in rows] == [GUARD_B_ID, GUARD_A_ID]
    assert rows[0].name == "DNS 异常恢复"
    assert rows[0].template == "network.up"
    assert rows[0].enabled is True
    assert len(rows[0].config_hash) == 32
    assert json.loads(rows[0].config_json)["id"] == GUARD_B_ID

    # 下发：存在配置时附带 guards，且 ETag 随内容变化
    delivered = _get_config(client, auth, registered)
    assert delivered.status_code == 200
    delivered_body = delivered.json()
    assert [item["id"] for item in delivered_body["guards"]] == [
        GUARD_B_ID,
        GUARD_A_ID,
    ]
    assert delivered_body["guards"][0]["name"] == "DNS 异常恢复"
    delivered_etag = delivered.headers["ETag"]
    assert delivered_etag != legacy_etag

    # 304：新 ETag 命中；旧 ETag 应返回 200
    assert _get_config(client, auth, registered, delivered_etag).status_code == 304
    stale = _get_config(client, auth, registered, legacy_etag)
    assert stale.status_code == 200
    assert "guards" in stale.json()

    # 整体替换：只保留 A 且改名，B 被删除
    replaced = client.put(
        _guards_url(client_id), json={"guards": [_guard(GUARD_A_ID, "断网恢复 v2")]}
    )
    assert replaced.status_code == 200
    replaced_body = replaced.json()
    assert [item["id"] for item in replaced_body["items"]] == [GUARD_A_ID]
    assert replaced_body["items"][0]["name"] == "断网恢复 v2"

    replaced_config = _get_config(client, auth, registered)
    assert replaced_config.headers["ETag"] != delivered_etag
    assert [item["id"] for item in replaced_config.json()["guards"]] == [GUARD_A_ID]

    with session_scope() as db:
        actions = db.scalars(select(AuditLog.action)).all()
        stored = db.scalars(
            select(GuardConfig).where(GuardConfig.client_id == client_id)
        ).all()
    assert actions.count("guards_update") == 2
    assert len(stored) == 1


def test_guards_delete_restores_legacy_config(
    client: TestClient,
    registered: dict[str, Any],
    auth: Callable[..., dict[str, str]],
) -> None:
    client_id = registered["client_id"]
    _login(client)

    legacy_etag = _get_config(client, auth, registered).headers["ETag"]

    assert (
        client.put(
            _guards_url(client_id), json={"guards": [_guard(GUARD_A_ID)]}
        ).status_code
        == 200
    )
    assert "guards" in _get_config(client, auth, registered).json()

    cleared = client.delete(_guards_url(client_id))
    assert cleared.status_code == 200
    assert cleared.json() == {"items": [], "managed": False}
    assert client.get(_guards_url(client_id)).json() == {
        "items": [],
        "managed": False,
    }

    # 删除后下发回退到无 guards 的旧结构，ETag 与最初一致
    fallback = _get_config(client, auth, registered)
    assert fallback.status_code == 200
    assert "guards" not in fallback.json()
    assert fallback.headers["ETag"] == legacy_etag
    assert _get_config(client, auth, registered, legacy_etag).status_code == 304

    with session_scope() as db:
        rows = db.scalars(
            select(GuardConfig).where(GuardConfig.client_id == client_id)
        ).all()
        actions = db.scalars(select(AuditLog.action)).all()
    assert rows == []
    assert "guards_clear" in actions


def test_guards_are_per_client(
    client: TestClient,
    registered: dict[str, Any],
    auth: Callable[..., dict[str, str]],
) -> None:
    """Guard 配置按客户端隔离，不影响其他客户端的 config 下发。"""
    _login(client)
    assert (
        client.put(
            _guards_url(registered["client_id"]),
            json={"guards": [_guard(GUARD_A_ID)]},
        ).status_code
        == 200
    )

    other = client.post(
        "/api/v1/register",
        headers={"X-Vigil-Bootstrap": "test-bootstrap"},
        json={
            "name": "router-b",
            "model": "iStoreOS",
            "fw_version": "24.10",
            "agent_version": "0.1.0",
            "protocol": 1,
        },
    )
    assert other.status_code == 200
    other_registered = other.json()

    other_config = client.get(
        "/api/v1/config",
        headers=auth(other_registered["client_id"], other_registered["secret"]),
    )
    assert other_config.status_code == 200
    assert "guards" not in other_config.json()


def test_guards_invalid_payloads(
    client: TestClient, registered: dict[str, Any]
) -> None:
    client_id = registered["client_id"]
    _login(client)

    valid = _guard(GUARD_A_ID)
    invalid_bodies: list[dict[str, Any]] = [
        {},  # 缺少 guards
        {"guards": "not-a-list"},
        {"guards": {}},
        {"guards": ["not-an-object"]},
        {"guards": [{**valid, "id": "not-a-uuid"}]},
        {"guards": [{**valid, "id": ""}]},
        {"guards": [{**valid, "name": "  "}]},
        {"guards": [{**valid, "name": 123}]},
        {"guards": [{**valid, "checks": []}]},
        {"guards": [{**valid, "checks": "carrier"}]},
        {"guards": [{**valid, "policy": "x"}]},
        {"guards": [{**valid, "policy": []}]},
        {"guards": [{**valid, "recovery": {}}]},
        {"guards": [valid, _guard(GUARD_A_ID, "重复项")]},  # guard_id 重复
        {
            "guards": [
                valid,
                _guard(GUARD_A_ID.upper(), "大写形式重复"),
            ]
        },
    ]
    for body in invalid_bodies:
        response = client.put(_guards_url(client_id), json=body)
        assert response.status_code == 400, (body, response.status_code, response.text)

    # 非对象 body 由 FastAPI 校验为 422
    assert client.put(_guards_url(client_id), json=[]).status_code == 422

    # 非法请求不应写入任何记录
    assert client.get(_guards_url(client_id)).json() == {
        "items": [],
        "managed": False,
    }


def test_guards_unknown_client(client: TestClient) -> None:
    _login(client)
    missing = "cl_does_not_exist"
    assert client.get(_guards_url(missing)).status_code == 404
    assert (
        client.put(_guards_url(missing), json={"guards": []}).status_code == 404
    )
    assert client.delete(_guards_url(missing)).status_code == 404
