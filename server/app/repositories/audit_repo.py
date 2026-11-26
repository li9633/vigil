"""审计日志写入与查询。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
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


def build_query(
    *,
    actor: str | None = None,
    action: str | None = None,
    target: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Select[tuple[AuditLog]]:
    """按过滤条件构造审计查询；时间参数已由调用方换算为 naive UTC。"""
    stmt = select(AuditLog)
    if actor:
        stmt = stmt.where(AuditLog.actor.contains(actor.strip()))
    if action:
        stmt = stmt.where(AuditLog.action.contains(action.strip()))
    if target:
        stmt = stmt.where(AuditLog.target.contains(target.strip()))
    if since is not None:
        stmt = stmt.where(AuditLog.ts >= since)
    if until is not None:
        stmt = stmt.where(AuditLog.ts <= until)
    return stmt


def count(db: Session, stmt: Select[tuple[AuditLog]]) -> int:
    return db.scalar(select(func.count()).select_from(stmt.subquery())) or 0


def list_rows(
    db: Session,
    stmt: Select[tuple[AuditLog]],
    *,
    limit: int,
    offset: int = 0,
    newest_first: bool = True,
) -> list[AuditLog]:
    if newest_first:
        stmt = stmt.order_by(AuditLog.ts.desc(), AuditLog.id.desc())
    else:
        stmt = stmt.order_by(AuditLog.ts.asc(), AuditLog.id.asc())
    return list(db.scalars(stmt.offset(offset).limit(limit)).all())
