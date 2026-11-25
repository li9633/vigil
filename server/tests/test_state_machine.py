"""状态机与 incident 生命周期测试。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import session_scope, utcnow
from app.models import Client, ClientEvent, Incident
from app.security import hash_secret
from app.settings import Thresholds
from app.state_machine import (
    SilenceState,
    SignalSnapshot,
    evaluate,
    is_flapping,
    recover_incidents,
    scan_once,
)

THRESHOLDS = Thresholds()


def _add_client(
    client_id: str = "cl_test",
    *,
    last_seen=None,
    last_beacon=None,
    boot_id: str | None = "boot-1",
) -> None:
    with session_scope() as db:
        db.add(
            Client(
                client_id=client_id,
                name="router",
                secret_sha256=hash_secret("secret"),
                boot_id=boot_id,
                last_seen=last_seen,
                last_beacon=last_beacon,
            )
        )


def test_evaluate_suspect_then_down() -> None:
    t0 = utcnow()
    snapshot = SignalSnapshot(last_seen=t0, last_beacon=t0)

    assert evaluate(t0 + timedelta(seconds=89), snapshot, THRESHOLDS).state is SilenceState.HEALTHY

    suspect = evaluate(t0 + timedelta(seconds=91), snapshot, THRESHOLDS)
    assert suspect.state is SilenceState.SUSPECT
    assert suspect.category == "agent_down"
    assert suspect.is_down is False

    down = evaluate(t0 + timedelta(seconds=151), snapshot, THRESHOLDS)
    assert down.state is SilenceState.AGENT_DOWN
    assert down.category == "agent_down"
    assert down.is_down is True


def test_agent_down_vs_host_or_net_down() -> None:
    t0 = utcnow()
    # beacon 新鲜（10s 前）→ agent 掉线
    fresh_beacon = SignalSnapshot(last_seen=t0, last_beacon=t0 - timedelta(seconds=10))
    assert (
        evaluate(t0 + timedelta(seconds=151), fresh_beacon, THRESHOLDS).state
        is SilenceState.AGENT_DOWN
    )

    # beacon 同时超过 180s → 主机或网络故障
    stale_beacon = SignalSnapshot(last_seen=t0, last_beacon=t0)
    result = evaluate(t0 + timedelta(seconds=181), stale_beacon, THRESHOLDS)
    assert result.state is SilenceState.HOST_OR_NET_DOWN
    assert result.category == "host_or_net_down"


def test_scan_suspect_then_down(db_ready: None) -> None:
    t0 = utcnow()
    _add_client(last_seen=t0, last_beacon=t0)

    with session_scope() as db:
        touched = scan_once(db, t0 + timedelta(seconds=91), THRESHOLDS)
        assert [incident.state for incident in touched] == ["SUSPECT"]

        # 重复扫描不会产生第二个 incident
        again = scan_once(db, t0 + timedelta(seconds=120), THRESHOLDS)
        assert len(again) == 1

    with session_scope() as db:
        touched = scan_once(db, t0 + timedelta(seconds=151), THRESHOLDS)
        assert [incident.state for incident in touched] == ["DOWN"]
        assert touched[0].category == "agent_down"

    with session_scope() as db:
        total = db.scalar(select(func.count()).select_from(Incident))
        assert total == 1


def test_scan_recovers_after_heartbeat_returns(db_ready: None) -> None:
    t0 = utcnow()
    _add_client(last_seen=t0, last_beacon=t0)

    with session_scope() as db:
        scan_once(db, t0 + timedelta(seconds=200), THRESHOLDS)

    with session_scope() as db:
        client = db.scalars(select(Client).where(Client.client_id == "cl_test")).one()
        client.last_seen = t0 + timedelta(seconds=210)

    with session_scope() as db:
        touched = scan_once(db, t0 + timedelta(seconds=211), THRESHOLDS)
        assert len(touched) == 1
        assert touched[0].state == "RECOVERED"
        assert touched[0].recovered_at is not None


def test_heartbeat_marks_incident_recovered(
    client: TestClient, registered: dict[str, Any], auth: Callable[..., dict[str, str]]
) -> None:
    now = utcnow()
    with session_scope() as db:
        row = db.scalars(
            select(Client).where(Client.client_id == registered["client_id"])
        ).one()
        row.last_seen = now - timedelta(seconds=200)
        row.last_beacon = now - timedelta(seconds=10)

    with session_scope() as db:
        scan_once(db, now, THRESHOLDS)
        incident = db.scalars(select(Incident)).one()
        assert incident.state == "DOWN"
        assert incident.category == "agent_down"

    response = client.post(
        "/api/v1/heartbeat",
        headers=auth(registered["client_id"], registered["secret"]),
        json={
            "client_id": registered["client_id"],
            "seq": 2,
            "boot_id": "boot-1",
            "uptime": 240,
            "agent_version": "0.1.0",
            "wan_ip": "203.0.113.7",
            "modules": {},
            "spool": {"pending": 0, "dropped": 0},
        },
    )
    assert response.status_code == 200

    with session_scope() as db:
        incident = db.scalars(select(Incident)).one()
        assert incident.state == "RECOVERED"
        assert incident.recovered_at is not None


def test_device_reboot_is_recorded(
    client: TestClient, registered: dict[str, Any], auth: Callable[..., dict[str, str]]
) -> None:
    now = utcnow()
    with session_scope() as db:
        row = db.scalars(
            select(Client).where(Client.client_id == registered["client_id"])
        ).one()
        row.boot_id = "boot-old"
        row.last_seen = now - timedelta(seconds=200)
        row.last_beacon = now - timedelta(seconds=10)

    with session_scope() as db:
        scan_once(db, now, THRESHOLDS)

    response = client.post(
        "/api/v1/heartbeat",
        headers=auth(registered["client_id"], registered["secret"]),
        json={
            "client_id": registered["client_id"],
            "seq": 3,
            "boot_id": "boot-new",
            "uptime": 20,
            "agent_version": "0.1.0",
            "wan_ip": "203.0.113.8",
            "modules": {},
        },
    )
    assert response.status_code == 200

    with session_scope() as db:
        incident = db.scalars(select(Incident)).one()
        assert incident.state == "RECOVERED"
        assert "设备重启" in incident.summary
        reboot_events = db.scalars(
            select(ClientEvent).where(ClientEvent.type == "device_reboot")
        ).all()
        assert len(reboot_events) == 1


def test_is_flapping_window() -> None:
    now = utcnow()
    recent = [now - timedelta(minutes=1), now - timedelta(minutes=2), now - timedelta(minutes=3)]
    old = [now - timedelta(minutes=1), now - timedelta(minutes=2), now - timedelta(hours=2)]

    assert is_flapping(recent, now) is True
    assert is_flapping(old, now) is False
    assert is_flapping(recent[:2], now) is False


def test_recovery_marks_flapping(db_ready: None) -> None:
    now = utcnow()
    with session_scope() as db:
        db.add(
            Client(
                client_id="cl_flap",
                name="flappy",
                secret_sha256=hash_secret("secret"),
                last_seen=now - timedelta(seconds=200),
                last_beacon=now - timedelta(seconds=10),
            )
        )
        for index in range(2):
            db.add(
                Incident(
                    client_id="cl_flap",
                    category="agent_down",
                    state="RECOVERED",
                    started_at=now - timedelta(minutes=20 + index),
                    detected_at=now - timedelta(minutes=19 + index),
                    recovered_at=now - timedelta(minutes=15 - index),
                    summary="历史故障",
                    evidence_json="{}",
                )
            )
        db.add(
            Incident(
                client_id="cl_flap",
                category="agent_down",
                state="DOWN",
                started_at=now - timedelta(seconds=200),
                detected_at=now - timedelta(seconds=190),
                summary="当前故障",
                evidence_json="{}",
            )
        )

    with session_scope() as db:
        recovered = recover_incidents(db, "cl_flap", now)
        assert len(recovered) == 1
        assert recovered[0].state == "RECOVERED"

    with session_scope() as db:
        latest = db.scalars(
            select(Incident)
            .where(Incident.client_id == "cl_flap")
            .order_by(Incident.recovered_at.desc())
        ).first()
        assert latest is not None
        assert "flapping" in latest.summary
