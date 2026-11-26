"""数据保留清理测试。"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.db import session_scope, utcnow
from app.models import Beacon, Client, ClientEvent, Heartbeat
from app.retention import purge_expired


def _seed(db, now) -> None:
    db.add(Client(client_id="c1", name="router", secret_sha256="x" * 64))
    db.add(Heartbeat(client_id="c1", received_at=now - timedelta(days=40), seq=1))
    db.add(Heartbeat(client_id="c1", received_at=now - timedelta(days=1), seq=2))
    db.add(Beacon(client_id="c1", received_at=now - timedelta(days=40)))
    db.add(ClientEvent(client_id="c1", event_id="old", received_at=now - timedelta(days=40)))
    db.add(ClientEvent(client_id="c1", event_id="new", received_at=now))


def test_purge_removes_rows_beyond_retention(db_ready: None) -> None:
    now = utcnow()
    with session_scope() as db:
        _seed(db, now)

    with session_scope() as db:
        removed = purge_expired(db, now, retention_days=30)

    assert removed == {"heartbeats": 1, "beacons": 1, "client_events": 1}
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(Heartbeat)) == 1
        assert db.scalar(select(func.count()).select_from(Beacon)) == 0
        assert db.scalar(select(func.count()).select_from(ClientEvent)) == 1


def test_purge_disabled_keeps_everything(db_ready: None) -> None:
    now = utcnow()
    with session_scope() as db:
        _seed(db, now)

    with session_scope() as db:
        assert purge_expired(db, now, retention_days=0) == {}

    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(Heartbeat)) == 2
