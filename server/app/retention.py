"""数据保留策略：清理过期的心跳、beacon 与客户端事件。

incident、审计日志与通知记录不在此清理范围内，保留完整告警历史。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast

from sqlalchemy import delete
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.models import Beacon, ClientEvent, Heartbeat

PURGE_MODELS = (Heartbeat, Beacon, ClientEvent)


def purge_expired(db: Session, now: datetime, retention_days: int) -> dict[str, int]:
    """删除早于保留期的明细数据，返回各表删除条数。"""
    if retention_days <= 0:
        return {}
    cutoff = now - timedelta(days=retention_days)
    removed: dict[str, int] = {}
    for model in PURGE_MODELS:
        result = cast(CursorResult, db.execute(delete(model).where(model.received_at < cutoff)))
        removed[model.__tablename__] = int(result.rowcount or 0)
    db.flush()
    return removed
