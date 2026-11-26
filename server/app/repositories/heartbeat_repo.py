"""心跳数据访问。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Heartbeat


def latest(db: Session, client_id: str) -> Heartbeat | None:
    return db.scalars(
        select(Heartbeat)
        .where(Heartbeat.client_id == client_id)
        .order_by(Heartbeat.received_at.desc())
        .limit(1)
    ).one_or_none()


def list_for_client(db: Session, client_id: str, limit: int = 100) -> list[Heartbeat]:
    return list(
        db.scalars(
            select(Heartbeat)
            .where(Heartbeat.client_id == client_id)
            .order_by(Heartbeat.received_at.desc())
            .limit(limit)
        ).all()
    )


def add(
    db: Session,
    *,
    client_id: str,
    received_at: datetime,
    seq: int,
    uptime: int,
    boot_id: str,
    wan_ip: str | None,
    checks: dict[str, Any],
    probes: list[dict[str, Any]],
    guards: dict[str, Any],
) -> Heartbeat:
    row = Heartbeat(
        client_id=client_id,
        received_at=received_at,
        seq=seq,
        uptime=uptime,
        boot_id=boot_id,
        wan_ip=wan_ip,
        checks_json=json.dumps(checks, ensure_ascii=False),
        probes_json=json.dumps(probes, ensure_ascii=False),
        guards_json=json.dumps(guards, ensure_ascii=False),
    )
    db.add(row)
    return row
