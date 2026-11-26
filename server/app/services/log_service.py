"""服务端结构化日志查询与导出用例。"""

from __future__ import annotations

import time
from typing import Any

from app.errors import ValidationError
from app.logstore import collect_log_events, export_log_events, log_paths
from app.settings import Settings

INSTANCE_CHOICES = ("api", "worker")


def _ensure_instance(instance: str | None) -> None:
    if instance and instance not in INSTANCE_CHOICES:
        raise ValidationError("instance 必须是 api 或 worker")


def list_logs(
    settings: Settings,
    *,
    limit: int = 200,
    level: str | None = None,
    event: str | None = None,
    client_id: str | None = None,
    request_id: str | None = None,
    instance: str | None = None,
    since: float | None = None,
) -> list[dict[str, Any]]:
    """合并 api / worker 两个实例的日志，按时间返回最近若干条。"""
    _ensure_instance(instance)
    return collect_log_events(
        log_paths(settings),
        limit=limit,
        level=level,
        event=event,
        client_id=client_id,
        request_id=request_id,
        instance=instance,
        since=since,
        backups=settings.log_backups,
    )


def export_logs(
    settings: Settings,
    *,
    level: str | None = None,
    event: str | None = None,
    client_id: str | None = None,
    request_id: str | None = None,
    instance: str | None = None,
    since: float | None = None,
) -> tuple[bytes, str]:
    """导出服务端日志为 JSONL，返回 (内容, 文件名)。"""
    _ensure_instance(instance)
    data = export_log_events(
        log_paths(settings),
        level=level,
        event=event,
        client_id=client_id,
        request_id=request_id,
        instance=instance,
        since=since,
        backups=settings.log_backups,
    )
    filename = "vigil-server-logs-%s.jsonl" % time.strftime("%Y%m%d-%H%M%S")
    return data, filename
