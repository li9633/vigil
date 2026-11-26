"""M8-7 压测演练：高频心跳与事件突发的正确性、幂等与耗时。"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import session_scope
from app.models import ClientEvent, Heartbeat

BOOTSTRAP_TOKEN = "test-bootstrap"
HEARTBEATS = 120
EVENT_BATCHES = 20
BATCH_SIZE = 50


def _register(client: TestClient) -> tuple[str, str]:
    response = client.post(
        "/api/v1/register",
        headers={"X-Vigil-Bootstrap": BOOTSTRAP_TOKEN},
        json={"name": "load-drill", "model": "iStoreOS", "agent_version": "0.1.0"},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    return data["client_id"], data["secret"]


def test_heartbeat_and_event_burst(
    client: TestClient, auth: Callable[..., dict[str, str]]
) -> None:
    client_id, secret = _register(client)

    started = time.perf_counter()
    for seq in range(1, HEARTBEATS + 1):
        response = client.post(
            "/api/v1/heartbeat",
            headers=auth(client_id, secret),
            json={
                "client_id": client_id,
                "seq": seq,
                "boot_id": "boot-load",
                "uptime": seq,
                "checks": {
                    "wan-link": {
                        "check_id": "wan-link",
                        "name": "WAN",
                        "probe": "carrier",
                        "status": "ok",
                        "latency_ms": 1,
                    }
                },
                "probes": [{"id": "carrier", "name": "物理链路", "source": "builtin"}],
                "guards": {
                    "g1": {"guard_id": "g1", "name": "网络", "status": "OK", "source": "server"}
                },
                "spool": {"pending": 0, "dropped": 0},
            },
        )
        assert response.status_code == 200, response.text

    last_batch: list[dict[str, Any]] = []
    for batch_index in range(EVENT_BATCHES):
        last_batch = [
            {
                "event_id": f"evt-{batch_index}-{index}",
                "seq": batch_index * BATCH_SIZE + index + 1,
                "type": "check_status",
                "domain": "network",
                "payload": {"n": index},
            }
            for index in range(BATCH_SIZE)
        ]
        response = client.post(
            "/api/v1/events",
            headers=auth(client_id, secret),
            json={"client_id": client_id, "events": last_batch},
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["acked"] == [item["event_id"] for item in last_batch]

    elapsed = time.perf_counter() - started
    print(
        "M8-7 load drill: heartbeats=%d events=%d elapsed=%.2fs"
        % (HEARTBEATS, EVENT_BATCHES * BATCH_SIZE, elapsed)
    )
    # 宽松上限，只拦截数量级退化
    assert elapsed < 30.0

    # 重放最后一批：幂等，不新增记录
    replay = client.post(
        "/api/v1/events",
        headers=auth(client_id, secret),
        json={"client_id": client_id, "events": last_batch},
    )
    assert replay.status_code == 200

    with session_scope() as db:
        heartbeat_count = db.scalar(
            select(func.count()).select_from(Heartbeat).where(Heartbeat.client_id == client_id)
        )
        event_count = db.scalar(
            select(func.count()).select_from(ClientEvent).where(ClientEvent.client_id == client_id)
        )
    assert heartbeat_count == HEARTBEATS
    assert event_count == EVENT_BATCHES * BATCH_SIZE
