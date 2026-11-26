"""管理端审计日志查询与导出接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.api.responses import ApiResponse, ok
from app.db import get_session
from app.schemas.admin import AuditListResponse
from app.services import audit_service

router = APIRouter()


@router.get("/audit")
def list_audit_logs(
    actor: str | None = Query(default=None),
    action: str | None = Query(default=None),
    target: str | None = Query(default=None),
    since: float | None = Query(default=None),
    until: float | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> ApiResponse[AuditListResponse]:
    """审计日志查询：登录、配置变更、手动操作。"""
    return ok(
        audit_service.list_audit(
            db,
            actor=actor,
            action=action,
            target=target,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )
    )


@router.get("/audit/export")
def export_audit_logs(
    actor: str | None = Query(default=None),
    action: str | None = Query(default=None),
    target: str | None = Query(default=None),
    since: float | None = Query(default=None),
    until: float | None = Query(default=None),
    db: Session = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> Response:
    """按过滤条件导出审计日志为 JSONL。"""
    content, filename = audit_service.export_audit(
        db,
        actor=actor,
        action=action,
        target=target,
        since=since,
        until=until,
    )
    return Response(
        content=content,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="%s"' % filename},
    )
