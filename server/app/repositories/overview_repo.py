"""总览页聚合查询。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Client, Incident


def count_clients(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Client)) or 0


def count_online_clients(db: Session, since: datetime) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Client)
            .where(Client.last_seen.is_not(None), Client.last_seen >= since)
        )
        or 0
    )


def count_open_incidents(db: Session) -> int:
    return (
        db.scalar(
            select(func.count()).select_from(Incident).where(Incident.recovered_at.is_(None))
        )
        or 0
    )


def count_incidents_since(db: Session, since: datetime) -> int:
    return (
        db.scalar(
            select(func.count()).select_from(Incident).where(Incident.detected_at >= since)
        )
        or 0
    )


def recent_incidents(db: Session, limit: int = 20) -> list[Incident]:
    return list(
        db.scalars(select(Incident).order_by(Incident.detected_at.desc()).limit(limit)).all()
    )


def flapping_client_counts(
    db: Session, *, since: datetime, categories: tuple[str, ...]
) -> list[tuple[str, int]]:
    rows = db.execute(
        select(Incident.client_id, func.count())
        .where(
            Incident.recovered_at.is_not(None),
            Incident.recovered_at >= since,
            Incident.guard_id.is_(None),
            Incident.category.in_(categories),
        )
        .group_by(Incident.client_id)
    ).all()
    return [(client_id, count) for client_id, count in rows]
