"""审计日志写入。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import AuditLog


def add_audit(
    db: Session,
    *,
    actor: str,
    action: str,
    target: str,
    detail: Any = None,
    ts: datetime | None = None,
) -> AuditLog:
    """追加一条审计记录；是否提交由调用方（service）决定。"""
    record = AuditLog(
        actor=actor,
        action=action,
        target=target,
        ts=ts or utcnow(),
        detail=json.dumps(detail if detail is not None else {}, ensure_ascii=False),
    )
    db.add(record)
    return record
