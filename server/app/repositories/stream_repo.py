"""SSE 推送所需的业务表聚合版本查询。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Client, GuardConfig, Incident, Notification


def snapshot_versions(db: Session) -> dict[str, tuple[Any, ...]]:
    """读取各业务表的聚合版本信息，不返回具体记录。"""
    incidents = db.execute(
        select(
            func.count(Incident.id),
            func.max(Incident.id),
            func.max(Incident.recovered_at),
            func.max(Incident.acked_at),
            func.max(Incident.notified_at),
        )
    ).one()
    clients = db.execute(
        select(func.count(Client.id), func.max(Client.created_at))
    ).one()
    guards = db.execute(
        select(func.count(GuardConfig.guard_id), func.max(GuardConfig.updated_at))
    ).one()
    notifications = db.execute(
        select(
            func.count(Notification.id),
            func.max(Notification.id),
            func.max(Notification.sent_at),
        )
    ).one()
    return {
        "incidents": tuple(incidents),
        "clients": tuple(clients),
        "guards": tuple(guards),
        "notifications": tuple(notifications),
    }
