"""管理端服务端日志查询与导出接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import require_admin
from app.api.responses import ApiResponse, ok
from app.services import log_service
from app.settings import Settings, settings_from_app

router = APIRouter()


@router.get("/logs")
def server_logs(
    limit: int = Query(200, ge=1, le=1000),
    level: str | None = None,
    event: str | None = None,
    client_id: str | None = None,
    request_id: str | None = None,
    instance: str | None = None,
    since: float | None = None,
    settings: Settings = Depends(settings_from_app),
    _actor: str = Depends(require_admin),
) -> ApiResponse[list[dict[str, Any]]]:
    """服务端结构化日志（合并 api / worker 两个实例）。"""
    return ok(
        log_service.list_logs(
            settings,
            limit=limit,
            level=level,
            event=event,
            client_id=client_id,
            request_id=request_id,
            instance=instance,
            since=since,
        )
    )


@router.get("/logs/export")
def server_logs_export(
    level: str | None = None,
    event: str | None = None,
    client_id: str | None = None,
    request_id: str | None = None,
    instance: str | None = None,
    since: float | None = None,
    settings: Settings = Depends(settings_from_app),
    _actor: str = Depends(require_admin),
) -> Response:
    """导出服务端日志为 JSONL。"""
    data, filename = log_service.export_logs(
        settings,
        level=level,
        event=event,
        client_id=client_id,
        request_id=request_id,
        instance=instance,
        since=since,
    )
    return Response(
        content=data,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="%s"' % filename},
    )
