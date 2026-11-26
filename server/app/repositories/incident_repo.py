"""告警事件数据访问。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Incident


def latest_by_client(db: Session, client_ids: list[str]) -> dict[str, Incident]:
    """批量取每个客户端最近一条 incident。"""
    if not client_ids:
        return {}
    rows = db.scalars(
        select(Incident)
        .where(Incident.client_id.in_(client_ids))
        .order_by(Incident.detected_at.desc())
    ).all()
    latest: dict[str, Incident] = {}
    for incident in rows:
        if incident.client_id not in latest:
            latest[incident.client_id] = incident
    return latest


def list_incidents(
    db: Session,
    *,
    client_id: str | None = None,
    state: str | None = None,
    category: str | None = None,
    limit: int = 100,
) -> list[Incident]:
    stmt = select(Incident)
    if client_id:
        stmt = stmt.where(Incident.client_id == client_id)
    if state:
        stmt = stmt.where(Incident.state == state)
    if category:
        stmt = stmt.where(Incident.category == category)
    return list(db.scalars(stmt.order_by(Incident.detected_at.desc()).limit(limit)).all())


def list_for_client(db: Session, client_id: str, limit: int = 100) -> list[Incident]:
    return list_incidents(db, client_id=client_id, limit=limit)
