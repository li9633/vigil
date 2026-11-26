"""服务端检测项与守护项配置管理端 CRUD、refs 校验与 /api/v1/config 下发测试。"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import session_scope
from app.models import AuditLog, CheckConfig, GuardConfig

# 两个 UUID，B 的字典序小于 A，用于验证按 guard_id 排序
GUARD_A_ID = "8f14e45f-ceea-467f-a8d3-2b1f0c9d7e61"
GUARD_B_ID = "0f1f5a3c-3a2f-4b0d-9c1e-2d3a4b5c6d7e"


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/v1/admin/login",
        json={"username": "admin", "password": "test-pass"},
    )
    assert response.status_code == 200, response.text


def _check(
    check_id: str,
    name: str | None = None,
    probe: str = "carrier",
    params: dict[str, Any] | None = None,
    expect: dict[str, Any] | None = None,
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "name": name or check_id,
        "probe": probe,
        "params": params if params is not None else {},
        "expect": expect if expect is not None else {"kind": "carrier", "up": True},
        "timeout": 3,
        "depends_on": depends_on or [],
        "enabled": True,
    }


def _guard(
    guard_id: str,
    name: str = "断网自动恢复",
    refs: list[str] | None = None,
    template: str = "network.up",
) -> dict[str, Any]:
    """符合设计文档 3.5 的完整 Guard 配置。"""
    return {
        "id": guard_id,
        "name": name,
        "template": template,
        "enabled": True,
        "check_refs": refs or ["wan-link", "pppoe-session"],
        "evaluate": {"mode": "dependency_tree", "quorum": 1},
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
    }


def _checks_url(client_id: str) -> str:
    return f"/api/v1/admin/clients/{client_id}/checks"


def _guards_url(client_id: str) -> str:
    return f"/api/v1/admin/clients/{client_id}/guards"


def _put_checks(client: TestClient, client_id: str, checks: list[dict[str, Any]]) -> Any:
    return client.put(_checks_url(client_id), json={"checks": checks})


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


def test_checks_and_guards_require_login(
    client: TestClient, registered: dict[str, Any]
) -> None:
    client_id = registered["client_id"]
    assert client.get(_checks_url(client_id)).status_code == 401
    assert _put_checks(client, client_id, []).status_code == 401
    assert client.get(f"/api/v1/admin/clients/{client_id}/probes").status_code == 401
    assert client.get(_guards_url(client_id)).status_code == 401
    assert client.put(_guards_url(client_id), json={"guards": []}).status_code == 401
    assert client.delete(_guards_url(client_id)).status_code == 401


def test_checks_crud_and_guard_refs(
    client: TestClient,
    registered: dict[str, Any],
    auth: Callable[..., dict[str, str]],
) -> None:
    client_id = registered["client_id"]
    _login(client)

    # 初始：无服务端配置，config 保持 schema v3 但不含 checks/guards
    initial = client.get(_checks_url(client_id))
    assert initial.status_code == 200
    assert initial.json()["data"] == {"items": [], "managed": False}

    legacy = _get_config(client, auth, registered)
    assert legacy.status_code == 200
    legacy_etag = legacy.headers["ETag"]
    assert legacy.json()["data"]["schema_version"] == 3
    assert "checks" not in legacy.json()["data"]
    assert "guards" not in legacy.json()["data"]

    # Guard 引用了不存在的检测项，必须拒绝
    missing_ref = _guard(GUARD_A_ID, refs=["not-there"])
    rejected = client.put(_guards_url(client_id), json={"guards": [missing_ref]})
    assert rejected.status_code == 400
    assert "not-there" in rejected.text

    # 整体写入 checks（顺序故意打乱），响应按 check_id 排序
    checks = [
        _check("wan-link", "WAN 物理链路", "carrier", {"device": "auto"}),
        _check(
            "pppoe-session",
            "PPPoE 会话",
            "pppoe",
            {"interface": "wan"},
            {"kind": "pppoe", "up": True},
            ["wan-link"],
        ),
    ]
    saved_checks = _put_checks(client, client_id, list(reversed(checks)))
    assert saved_checks.status_code == 200, saved_checks.text
    body = saved_checks.json()["data"]
    assert body["managed"] is True
    assert [item["id"] for item in body["items"]] == ["pppoe-session", "wan-link"]
    assert body["items"][1]["params"] == {"device": "auto"}
    assert body["items"][0]["depends_on"] == ["wan-link"]

    # 持久化到 checks 表，元数据列齐全
    with session_scope() as db:
        rows = db.scalars(
            select(CheckConfig)
            .where(CheckConfig.client_id == client_id)
            .order_by(CheckConfig.check_id)
        ).all()
    assert [row.check_id for row in rows] == ["pppoe-session", "wan-link"]
    assert rows[1].probe == "carrier"
    assert len(rows[1].config_hash) == 32
    assert json.loads(rows[1].config_json)["id"] == "wan-link"

    # 仅 checks 时也下发 checks + 空 guards
    checks_only = _get_config(client, auth, registered)
    checks_only_body = checks_only.json()["data"]
    assert [item["id"] for item in checks_only_body["checks"]] == [
        "pppoe-session",
        "wan-link",
    ]
    assert checks_only_body["guards"] == []
    checks_only_etag = checks_only.headers["ETag"]
    assert checks_only_etag != legacy_etag

    # 写入 guards（顺序打乱），响应按 guard_id 排序
    payload = {
        "guards": [
            _guard(GUARD_A_ID),
            _guard(GUARD_B_ID, "DNS 异常恢复", template=""),
        ]
    }
    saved = client.put(_guards_url(client_id), json=payload)
    assert saved.status_code == 200, saved.text
    saved_body = saved.json()["data"]
    assert saved_body["managed"] is True
    assert [item["id"] for item in saved_body["items"]] == [GUARD_B_ID, GUARD_A_ID]
    assert saved_body["items"][1]["check_refs"] == ["wan-link", "pppoe-session"]
    assert "updated_at" not in saved_body["items"][0]

    with session_scope() as db:
        rows = db.scalars(
            select(GuardConfig)
            .where(GuardConfig.client_id == client_id)
            .order_by(GuardConfig.guard_id)
        ).all()
    assert [row.guard_id for row in rows] == [GUARD_B_ID, GUARD_A_ID]
    assert rows[0].name == "DNS 异常恢复"
    assert rows[0].template == ""
    assert rows[1].template == "network.up"
    assert rows[0].enabled is True
    assert len(rows[0].config_hash) == 32
    assert json.loads(rows[0].config_json)["id"] == GUARD_B_ID

    # 下发：checks + guards 同时存在，ETag 随内容变化
    delivered = _get_config(client, auth, registered)
    assert delivered.status_code == 200
    delivered_body = delivered.json()["data"]
    assert delivered_body["schema_version"] == 3
    assert [item["id"] for item in delivered_body["checks"]] == [
        "pppoe-session",
        "wan-link",
    ]
    assert [item["id"] for item in delivered_body["guards"]] == [
        GUARD_B_ID,
        GUARD_A_ID,
    ]
    delivered_etag = delivered.headers["ETag"]
    assert delivered_etag != checks_only_etag

    # 304：新 ETag 命中；旧 ETag 返回 200
    assert _get_config(client, auth, registered, delivered_etag).status_code == 304
    stale = _get_config(client, auth, registered, legacy_etag)
    assert stale.status_code == 200
    assert "checks" in stale.json()["data"]

    # 整体替换：只保留 A 且改名，B 被删除
    replaced = client.put(
        _guards_url(client_id), json={"guards": [_guard(GUARD_A_ID, "断网恢复 v2")]}
    )
    assert replaced.status_code == 200
    replaced_body = replaced.json()["data"]
    assert [item["id"] for item in replaced_body["items"]] == [GUARD_A_ID]
    assert replaced_body["items"][0]["name"] == "断网恢复 v2"

    replaced_config = _get_config(client, auth, registered)
    assert replaced_config.headers["ETag"] != delivered_etag
    assert [item["id"] for item in replaced_config.json()["data"]["guards"]] == [GUARD_A_ID]

    with session_scope() as db:
        actions = db.scalars(select(AuditLog.action)).all()
        stored_guards = db.scalars(
            select(GuardConfig).where(GuardConfig.client_id == client_id)
        ).all()
    assert actions.count("checks_update") == 1
    assert actions.count("guards_update") == 2
    assert len(stored_guards) == 1


def test_checks_cannot_remove_referenced_ids(
    client: TestClient, registered: dict[str, Any]
) -> None:
    client_id = registered["client_id"]
    _login(client)

    assert _put_checks(
        client, client_id, [_check("wan-link", probe="carrier")]
    ).status_code == 200
    assert (
        client.put(
            _guards_url(client_id),
            json={"guards": [_guard(GUARD_A_ID, refs=["wan-link"])]},
        ).status_code
        == 200
    )

    rejected = _put_checks(client, client_id, [])
    assert rejected.status_code == 400
    assert "wan-link" in rejected.text

    # 原配置保持不变
    assert [item["id"] for item in client.get(_checks_url(client_id)).json()["data"]["items"]] == [
        "wan-link"
    ]


def test_clear_checks_and_guards_restores_local_config(
    client: TestClient,
    registered: dict[str, Any],
    auth: Callable[..., dict[str, str]],
) -> None:
    client_id = registered["client_id"]
    _login(client)

    legacy_etag = _get_config(client, auth, registered).headers["ETag"]

    assert _put_checks(
        client, client_id, [_check("wan-link", probe="carrier")]
    ).status_code == 200
    assert (
        client.put(
            _guards_url(client_id),
            json={"guards": [_guard(GUARD_A_ID, refs=["wan-link"])]},
        ).status_code
        == 200
    )
    assert "guards" in _get_config(client, auth, registered).json()["data"]

    cleared = client.delete(_guards_url(client_id))
    assert cleared.status_code == 200
    assert cleared.json()["data"] == {"items": [], "managed": False}
    # checks 仍由服务端管理，因此下发 checks + 空 guards
    managed_checks = _get_config(client, auth, registered).json()["data"]
    assert [item["id"] for item in managed_checks["checks"]] == ["wan-link"]
    assert managed_checks["guards"] == []

    empty = _put_checks(client, client_id, [])
    assert empty.status_code == 200
    assert empty.json()["data"] == {"items": [], "managed": False}

    # 两者都清空后回退本地配置，ETag 与最初一致
    fallback = _get_config(client, auth, registered)
    assert fallback.status_code == 200
    assert "checks" not in fallback.json()["data"]
    assert "guards" not in fallback.json()["data"]
    assert fallback.headers["ETag"] == legacy_etag
    assert _get_config(client, auth, registered, legacy_etag).status_code == 304

    with session_scope() as db:
        check_rows = db.scalars(
            select(CheckConfig).where(CheckConfig.client_id == client_id)
        ).all()
        guard_rows = db.scalars(
            select(GuardConfig).where(GuardConfig.client_id == client_id)
        ).all()
        actions = db.scalars(select(AuditLog.action)).all()
    assert check_rows == []
    assert guard_rows == []
    assert "guards_clear" in actions


def test_checks_and_guards_are_per_client(
    client: TestClient,
    registered: dict[str, Any],
    auth: Callable[..., dict[str, str]],
) -> None:
    """配置按客户端隔离，不影响其他客户端的 config 下发。"""
    _login(client)
    assert _put_checks(
        client, registered["client_id"], [_check("wan-link", probe="carrier")]
    ).status_code == 200
    assert (
        client.put(
            _guards_url(registered["client_id"]),
            json={"guards": [_guard(GUARD_A_ID, refs=["wan-link"])]},
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
    other_registered = other.json()["data"]

    other_config = client.get(
        "/api/v1/config",
        headers=auth(other_registered["client_id"], other_registered["secret"]),
    )
    assert other_config.status_code == 200
    assert "checks" not in other_config.json()["data"]
    assert "guards" not in other_config.json()["data"]


def test_invalid_checks_payloads(
    client: TestClient, registered: dict[str, Any]
) -> None:
    client_id = registered["client_id"]
    _login(client)

    valid = _check("wan-link", probe="carrier")
    invalid_bodies: list[dict[str, Any]] = [
        {},
        {"checks": "not-a-list"},
        {"checks": {}},
        {"checks": ["not-an-object"]},
        {"checks": [{**valid, "id": "Bad_ID"}]},
        {"checks": [{**valid, "id": ""}]},
        {"checks": [{**valid, "name": "  "}]},
        {"checks": [{**valid, "probe": "nope"}]},
        {"checks": [{**valid, "probe": "command"}]},
        {"checks": [{**valid, "params": []}]},
        {"checks": [{**valid, "params": {"device": 1}}]},
        {"checks": [{**valid, "expect": {"kind": "nope"}}]},
        {"checks": [{**valid, "expect": "nope"}]},
        {"checks": [{**valid, "expect": {"kind": "http", "body_contains": 1}}]},
        {"checks": [{**valid, "timeout": 0}]},
        {"checks": [{**valid, "timeout": 61}]},
        {"checks": [{**valid, "depends_on": "wan-link"}]},
        {"checks": [{**valid, "depends_on": ["wan-link"]}]},
        {"checks": [{**valid, "depends_on": ["missing"]}]},
        {
            "checks": [
                {**valid, "id": "a", "depends_on": ["b"]},
                {**valid, "id": "b", "depends_on": ["a"]},
            ]
        },
        {"checks": [valid, {**valid, "name": "重复项"}]},
    ]
    for body in invalid_bodies:
        response = client.put(_checks_url(client_id), json=body)
        assert response.status_code == 400, (body, response.status_code, response.text)

    # 非对象 body 由 FastAPI 校验为 422
    assert client.put(_checks_url(client_id), json=[]).status_code == 422

    # 非法请求不应写入任何记录
    assert client.get(_checks_url(client_id)).json()["data"] == {
        "items": [],
        "managed": False,
    }


def test_invalid_guard_payloads(
    client: TestClient, registered: dict[str, Any]
) -> None:
    client_id = registered["client_id"]
    _login(client)
    assert _put_checks(
        client, client_id, [_check("wan-link", probe="carrier")]
    ).status_code == 200

    valid = _guard(GUARD_A_ID, refs=["wan-link"])
    invalid_bodies: list[dict[str, Any]] = [
        {},
        {"guards": "not-a-list"},
        {"guards": {}},
        {"guards": ["not-an-object"]},
        {"guards": [{**valid, "id": "not-a-uuid"}]},
        {"guards": [{**valid, "id": ""}]},
        {"guards": [{**valid, "name": "  "}]},
        {"guards": [{**valid, "check_refs": []}]},
        {"guards": [{**valid, "check_refs": "wan-link"}]},
        {"guards": [{**valid, "check_refs": ["missing"]}]},
        {"guards": [{**valid, "check_refs": ["wan-link", "wan-link"]}]},
        # V1 内嵌 checks 结构不再接受（缺少 check_refs）
        {"guards": [{key: value for key, value in valid.items() if key != "check_refs"}]},
        {"guards": [{**valid, "policy": "x"}]},
        {"guards": [{**valid, "policy": []}]},
        {"guards": [{**valid, "recovery": {}}]},
        {"guards": [{**valid, "recovery": [{"action": "reboot"}]}]},
        {"guards": [{**valid, "evaluate": {"mode": "nope"}}]},
        {"guards": [{**valid, "evaluate": {"mode": "quorum", "quorum": 0}}]},
        {"guards": [valid, _guard(GUARD_A_ID, "重复项", refs=["wan-link"])]},
        {"guards": [valid, _guard(GUARD_A_ID.upper(), "大写形式重复", refs=["wan-link"])]},
    ]
    for body in invalid_bodies:
        response = client.put(_guards_url(client_id), json=body)
        assert response.status_code == 400, (body, response.status_code, response.text)

    assert client.put(_guards_url(client_id), json=[]).status_code == 422

    assert client.get(_guards_url(client_id)).json()["data"] == {
        "items": [],
        "managed": False,
    }


def test_probe_catalog_cache(
    client: TestClient,
    registered: dict[str, Any],
    auth: Callable[..., dict[str, str]],
) -> None:
    """管理端 probes 接口返回最近一次心跳上报的只读目录。"""
    client_id = registered["client_id"]
    _login(client)

    empty = client.get(f"/api/v1/admin/clients/{client_id}/probes")
    assert empty.status_code == 200
    assert empty.json()["data"] == {"items": [], "updated_at": None}

    heartbeat = client.post(
        "/api/v1/heartbeat",
        headers=auth(client_id, registered["secret"]),
        json={
            "client_id": client_id,
            "seq": 1,
            "boot_id": "boot-1",
            "uptime": 10,
            "checks": {"wan-link": {"check_id": "wan-link", "status": "ok"}},
            "probes": [
                {
                    "id": "carrier",
                    "name": "物理链路",
                    "version": "builtin",
                    "source": "builtin",
                    "remote_allowed": True,
                    "params_schema": {"device": {"type": "string"}},
                }
            ],
        },
    )
    assert heartbeat.status_code == 200

    catalog = client.get(f"/api/v1/admin/clients/{client_id}/probes")
    assert catalog.status_code == 200
    data = catalog.json()["data"]
    assert [item["id"] for item in data["items"]] == ["carrier"]
    assert data["updated_at"]


def test_config_unknown_client(client: TestClient) -> None:
    _login(client)
    missing = "cl_does_not_exist"
    assert client.get(_checks_url(missing)).status_code == 404
    assert client.put(_checks_url(missing), json={"checks": []}).status_code == 404
    assert client.get(f"/api/v1/admin/clients/{missing}/probes").status_code == 404
    assert client.get(_guards_url(missing)).status_code == 404
    assert client.put(_guards_url(missing), json={"guards": []}).status_code == 404
    assert client.delete(_guards_url(missing)).status_code == 404
