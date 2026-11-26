"""审计日志查询与导出用例。"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.repositories import audit_repo
from app.schemas.admin import AuditListResponse
from app.services import serializers

AUDIT_EXPORT_LIMIT = 5000


def _epoch_utc(value: float | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=UTC).replace(tzinfo=None)


def _query(
    *,
    actor: str | None,
    action: str | None,
    target: str | None,
    since: float | None,
    until: float | None,
):
    return audit_repo.build_query(
        actor=actor,
        action=action,
        target=target,
        since=_epoch_utc(since),
        until=_epoch_utc(until),
    )


def list_audit(
    db: Session,
    *,
    actor: str | None = None,
    action: str | None = None,
    target: str | None = None,
    since: float | None = None,
    until: float | None = None,
    limit: int = 100,
    offset: int = 0,
) -> AuditListResponse:
    """审计日志查询：登录、配置变更、手动操作。"""
    stmt = _query(actor=actor, action=action, target=target, since=since, until=until)
    total = audit_repo.count(db, stmt)
    rows = audit_repo.list_rows(db, stmt, limit=limit, offset=offset)
    return AuditListResponse(
        items=[serializers.audit_item(record) for record in rows],
        total=total,
    )


def export_audit(
    db: Session,
    *,
    actor: str | None = None,
    action: str | None = None,
    target: str | None = None,
    since: float | None = None,
    until: float | None = None,
) -> tuple[str, str]:
    """按过滤条件导出审计日志，返回 (JSONL 内容, 文件名)。"""
    stmt = _query(actor=actor, action=action, target=target, since=since, until=until)
    rows = audit_repo.list_rows(
        db, stmt, limit=AUDIT_EXPORT_LIMIT, newest_first=False
    )
    lines = [
        json.dumps(serializers.audit_item(record).model_dump(), ensure_ascii=False)
        for record in rows
    ]
    content = "\n".join(lines)
    if content:
        content += "\n"
    filename = "vigil-audit-%s.jsonl" % time.strftime("%Y%m%d-%H%M%S")
    return content, filename
