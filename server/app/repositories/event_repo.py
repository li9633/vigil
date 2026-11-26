"""客户端事件数据访问。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ClientEvent


def known_event_ids(db: Session, event_ids: list[str]) -> set[str]:
    """返回已入库的 event_id，供幂等去重。"""
    if not event_ids:
        return set()
    return set(
        db.scalars(
            select(ClientEvent.event_id).where(ClientEvent.event_id.in_(event_ids))
        ).all()
    )


def list_for_client(db: Session, client_id: str, limit: int = 100) -> list[ClientEvent]:
    return list(
        db.scalars(
            select(ClientEvent)
            .where(ClientEvent.client_id == client_id)
            .order_by(ClientEvent.received_at.desc())
            .limit(limit)
        ).all()
    )


def add(
    db: Session,
    *,
    client_id: str,
    event_id: str,
    seq: int,
    event_type: str,
    domain: str,
    payload: dict[str, Any],
    received_at: datetime,
) -> ClientEvent:
    row = ClientEvent(
        client_id=client_id,
        event_id=event_id,
        seq=seq,
        type=event_type,
        domain=domain,
        payload_json=json.dumps(payload, ensure_ascii=False),
        received_at=received_at,
    )
    db.add(row)
    return row
