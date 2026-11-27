"""Check / Guard 归客户端管理：服务端只读，配置下发不含二者。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from app.db import session_scope
from app.models import CheckConfig, GuardConfig

BOOTSTRAP_TOKEN = "test-bootstrap"


def _register(client: TestClient) -> tuple[str, str]:
    response = client.post(
        "/api/v1/register",
        headers={"X-Vigil-Bootstrap": BOOTSTRAP_TOKEN},
        json={"name": "readonly-client", "model": "iStoreOS", "agent_version": "0.1.0"},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    return data["client_id"], data["secret"]


def test_config_excludes_client_owned_checks_and_guards(
    client: TestClient, auth: Callable[..., dict[str, str]]
) -> None:
    client_id, secret = _register(client)

    # 即使库里存在历史配置，也不再下发
    with session_scope() as db:
        db.add(
            CheckConfig(
                client_id=client_id,
                check_id="wan-link",
                name="WAN",
                probe="carrier",
                config_json='{"id":"wan-link","probe":"carrier"}',
            )
        )
        db.add(
            GuardConfig(
                client_id=client_id,
                guard_id="6f2c1a4e-9b7d-4c3a-8e51-2d4b6a8f0c37",
                name="网络",
                config_json='{"id":"6f2c1a4e-9b7d-4c3a-8e51-2d4b6a8f0c37"}',
            )
        )

    config = client.get("/api/v1/config", headers=auth(client_id, secret))
    assert config.status_code == 200
    body = config.json()["data"]
    assert body["schema_version"] == 3
    assert "checks" not in body
    assert "guards" not in body


def test_admin_write_endpoints_removed(client: TestClient) -> None:
    assert client.put("/api/v1/admin/clients/c1/checks", json={"checks": []}).status_code in (
        401,
        404,
        405,
    )
    assert client.put("/api/v1/admin/clients/c1/guards", json={"guards": []}).status_code in (
        401,
        404,
        405,
    )
    assert client.delete("/api/v1/admin/clients/c1/guards").status_code in (401, 404, 405)


def test_probe_catalog_remains_read_only_view(
    client: TestClient, auth: Callable[..., dict[str, str]]
) -> None:
    client_id, secret = _register(client)
    heartbeat = client.post(
        "/api/v1/heartbeat",
        headers=auth(client_id, secret),
        json={
            "client_id": client_id,
            "seq": 1,
            "boot_id": "boot",
            "probes": [{"id": "carrier", "name": "物理链路", "source": "builtin"}],
        },
    )
    assert heartbeat.status_code == 200

    login = client.post(
        "/api/v1/admin/login", json={"username": "admin", "password": "test-pass"}
    )
    assert login.status_code == 200
    probes = client.get(f"/api/v1/admin/clients/{client_id}/probes")
    assert probes.status_code == 200
    items: list[dict[str, Any]] = probes.json()["data"]["items"]
    assert [item["id"] for item in items] == ["carrier"]
