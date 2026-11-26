"""管理端 API（M3 最小实现）：登录、总览、客户端、案卷、确认。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, require_admin
from app.api.responses import ApiResponse, ok
from app.db import get_session, session_scope, utcnow
from app.logstore import collect_log_events, export_log_events, log_event, log_paths
from app.models import (
    AuditLog,
    Client,
    GuardConfig,
    Incident,
    Notification,
)
from app.notifications import (
    build_message,
    load_config,
    mask_config,
    save_config,
    serialize_notification,
    validate_config,
)
from app.notifiers.base import SendResult
from app.notifiers.factory import build_notifiers, target_for
from app.policies import (
    default_policies,
    effective_settings,
    load_policies,
    save_policies,
)
from app.schemas import LoginRequest, LoginResponse, NotificationTestRequest, iso_utc
from app.schemas.admin import IncidentItem
from app.security import hash_secret, new_client_secret
from app.services import serializers
from app.services.auth_service import (  # noqa: F401 - 兼容旧调用方的 re-export
    LOGIN_MAX_FAILURES,
    login_admin,
    reset_login_limiter,
)
from app.settings import Settings, settings_from_app
from app.system_settings import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    admin_auth_state,
    set_admin_password,
    verify_admin_password,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
) -> ApiResponse[LoginResponse]:
    actor = login_admin(
        db,
        settings,
        username=payload.username,
        password=payload.password,
        client_key=client_ip(request),
    )
    request.session["admin"] = actor
    return ok(LoginResponse(actor=actor, server_time=int(time.time())))


@router.post("/incidents/{incident_id}/ack")
def ack_incident(
    incident_id: int,
    db: Session = Depends(get_session),
    actor: str = Depends(require_admin),
) -> IncidentItem:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident 不存在")

    if incident.acked_at is None:
        incident.acked_at = utcnow()
    db.add(
        AuditLog(
            actor=actor,
            action="ack_incident",
            target=str(incident.id),
            ts=utcnow(),
            detail=json.dumps(
                {"client_id": incident.client_id, "category": incident.category},
                ensure_ascii=False,
            ),
        )
    )
    db.commit()
    return serializers.incident_item(incident)


# --------------------------------------------------------------------------
# 通知配置与记录
# --------------------------------------------------------------------------


@router.get("/notifications")
def get_notifications(
    db: Session = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    config = load_config(db)
    records = db.scalars(
        select(Notification).order_by(Notification.id.desc()).limit(20)
    ).all()
    return {
        "config": mask_config(config),
        "records": [serialize_notification(record) for record in records],
    }


@router.put("/notifications")
def update_notifications(
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_session),
    actor: str = Depends(require_admin),
) -> dict[str, Any]:
    try:
        config = save_config(db, payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    db.add(
        AuditLog(
            actor=actor,
            action="update_notifications",
            target="notifications",
            ts=utcnow(),
            detail=json.dumps(
                {
                    "enabled": [
                        channel
                        for channel in ("feishu", "wecom", "email")
                        if config[channel]["enabled"]
                    ],
                    "rate_limit_per_hour": config["rate_limit_per_hour"],
                },
                ensure_ascii=False,
            ),
        )
    )
    db.commit()
    return {"config": mask_config(config)}


@router.post("/notifications/test")
def test_notifications(
    payload: NotificationTestRequest,
    db: Session = Depends(get_session),
    actor: str = Depends(require_admin),
) -> dict[str, Any]:
    config = load_config(db)
    try:
        validate_config(config)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    notifiers = build_notifiers(config)
    selected = (
        notifiers
        if payload.channel == "all"
        else [notifier for notifier in notifiers if notifier.channel == payload.channel]
    )
    if not selected:
        detail = (
            "没有已启用的通知渠道"
            if payload.channel == "all"
            else f"渠道 {payload.channel} 未配置或未启用"
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)

    now = utcnow()
    results: list[dict[str, Any]] = []
    for notifier in selected:
        message = build_message(None, "test", now, None, notifier.channel)
        try:
            result = notifier.send(message)
        except Exception as exc:  # noqa: BLE001 - 单渠道异常不影响其他渠道
            result = SendResult(False, f"{type(exc).__name__}: 发送异常")

        record = Notification(
            incident_id=None,
            client_id="",
            channel=notifier.channel,
            target=target_for(notifier.channel, config),
            kind="test",
            status="sent" if result.ok else "failed",
            attempts=1,
            last_error=None if result.ok else result.error[:500],
            next_attempt_at=None if result.ok else now + timedelta(seconds=60),
            created_at=now,
            sent_at=now if result.ok else None,
        )
        db.add(record)
        results.append(
            {"channel": notifier.channel, "ok": result.ok, "error": result.error}
        )

    db.add(
        AuditLog(
            actor=actor,
            action="test_notification",
            target=payload.channel,
            ts=utcnow(),
            detail=json.dumps(
                [{"channel": item["channel"], "ok": item["ok"]} for item in results],
                ensure_ascii=False,
            ),
        )
    )
    db.commit()
    return {"results": results}


@router.get("/notifications/records")
def list_notification_records(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    records = db.scalars(
        select(Notification).order_by(Notification.id.desc()).limit(limit)
    ).all()
    return {"records": [serialize_notification(record) for record in records]}


# --------------------------------------------------------------------------
# 服务端守护项（Guard）配置：整体读写，供 /api/v1/config 下发
# --------------------------------------------------------------------------


def _require_client_row(db: Session, client_id: str) -> Client:
    client = db.scalars(select(Client).where(Client.client_id == client_id)).one_or_none()
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="客户端不存在")
    return client


def _guard_config_hash(config: dict[str, Any]) -> str:
    body = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


def _normalize_guard(raw: Any, index: int) -> dict[str, Any]:
    """校验单个 Guard 配置并规范化 id / name。"""
    if not isinstance(raw, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"guards[{index}] 必须是对象",
        )

    raw_id = raw.get("id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"guards[{index}].id 必须是 UUID",
        )
    try:
        # 统一存小写标准形式，避免同一 UUID 因写法不同被当成两个
        guard_id = str(uuid.UUID(raw_id.strip()))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"guards[{index}].id 必须是 UUID",
        ) from exc

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"guards[{index}].name 不能为空",
        )

    checks = raw.get("checks")
    if not isinstance(checks, list) or not checks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"guards[{index}].checks 必须是非空数组",
        )

    if not isinstance(raw.get("policy"), dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"guards[{index}].policy 必须是对象",
        )

    if not isinstance(raw.get("recovery"), list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"guards[{index}].recovery 必须是数组",
        )

    return {**raw, "id": guard_id, "name": name.strip()}


def _serialize_guard_rows(rows: list[GuardConfig]) -> dict[str, Any]:
    """按 guard_id 升序返回配置对象；只暴露配置本身，不含存储元数据。"""
    items: list[dict[str, Any]] = []
    for row in rows:
        try:
            config = json.loads(row.config_json or "{}")
        except json.JSONDecodeError:
            continue
        if isinstance(config, dict):
            items.append(config)
    return {"items": items, "managed": bool(items)}


@router.get("/clients/{client_id}/guards")
def get_client_guards(
    client_id: str,
    db: Session = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    _require_client_row(db, client_id)
    rows = db.scalars(
        select(GuardConfig)
        .where(GuardConfig.client_id == client_id)
        .order_by(GuardConfig.guard_id)
    ).all()
    return _serialize_guard_rows(list(rows))


@router.put("/clients/{client_id}/guards")
def update_client_guards(
    client_id: str,
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_session),
    actor: str = Depends(require_admin),
) -> dict[str, Any]:
    _require_client_row(db, client_id)

    raw_guards = payload.get("guards")
    if not isinstance(raw_guards, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="guards 必须是数组"
        )

    configs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_guards):
        config = _normalize_guard(raw, index)
        guard_id = str(config["id"])
        if guard_id in seen:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"guard_id 重复: {guard_id}",
            )
        seen.add(guard_id)
        configs.append(config)

    now = utcnow()
    existing = {
        row.guard_id: row
        for row in db.scalars(
            select(GuardConfig).where(GuardConfig.client_id == client_id)
        ).all()
    }
    for config in configs:
        guard_id = str(config["id"])
        row = existing.get(guard_id)
        if row is None:
            row = GuardConfig(client_id=client_id, guard_id=guard_id)
            db.add(row)
        row.name = str(config["name"])
        row.template = str(config.get("template") or "")
        row.enabled = bool(config.get("enabled", True))
        row.config_json = json.dumps(config, ensure_ascii=False)
        row.config_hash = _guard_config_hash(config)
        row.updated_at = now

    # 整体替换：删除本次未提交的记录
    removed = 0
    for guard_id, row in existing.items():
        if guard_id not in seen:
            db.delete(row)
            removed += 1

    db.add(
        AuditLog(
            actor=actor,
            action="guards_update",
            target=client_id,
            ts=now,
            detail=json.dumps(
                {
                    "count": len(configs),
                    "removed": removed,
                    "guard_ids": [str(config["id"]) for config in configs],
                },
                ensure_ascii=False,
            ),
        )
    )
    db.commit()
    log_event(
        "info",
        "guard_config.update",
        "守护项配置已更新",
        client_id=client_id,
        actor=actor,
        count=len(configs),
        removed=removed,
    )

    rows = db.scalars(
        select(GuardConfig)
        .where(GuardConfig.client_id == client_id)
        .order_by(GuardConfig.guard_id)
    ).all()
    return _serialize_guard_rows(list(rows))


@router.delete("/clients/{client_id}/guards")
def clear_client_guards(
    client_id: str,
    db: Session = Depends(get_session),
    actor: str = Depends(require_admin),
) -> dict[str, Any]:
    _require_client_row(db, client_id)

    rows = db.scalars(
        select(GuardConfig).where(GuardConfig.client_id == client_id)
    ).all()
    removed = list(rows)
    for row in removed:
        db.delete(row)

    db.add(
        AuditLog(
            actor=actor,
            action="guards_clear",
            target=client_id,
            ts=utcnow(),
            detail=json.dumps({"removed": len(removed)}, ensure_ascii=False),
        )
    )
    db.commit()
    log_event(
        "info",
        "guard_config.clear",
        "守护项下发配置已清除",
        client_id=client_id,
        actor=actor,
        removed=len(removed),
    )
    return {"items": [], "managed": False}


# --------------------------------------------------------------------------
# 运行时策略配置
# --------------------------------------------------------------------------


@router.get("/policies")
def get_policies(
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    return {
        "policies": load_policies(db, settings),
        "defaults": default_policies(settings),
    }


@router.put("/policies")
def update_policies(
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
    actor: str = Depends(require_admin),
) -> dict[str, Any]:
    try:
        policies = save_policies(db, payload, settings)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    db.add(
        AuditLog(
            actor=actor,
            action="update_policies",
            target="policies",
            ts=utcnow(),
            detail=json.dumps(policies, ensure_ascii=False),
        )
    )
    db.commit()
    log_event("info", "policies.update", "运行时策略已更新", actor=actor, policies=policies)
    return {
        "policies": policies,
        "defaults": default_policies(settings),
    }


# --------------------------------------------------------------------------
# 系统设置：管理员密码与客户端密钥轮换
# --------------------------------------------------------------------------


@router.get("/system")
def get_system_settings(
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    effective = effective_settings(db, settings)
    state = admin_auth_state(db)
    return {
        "admin_username": settings.admin_username,
        "password_source": state["source"],
        "password_updated_at": state["updated_at"],
        "retention_days": effective.retention_days,
        "disk_alert_percent": effective.disk_alert_percent,
        "disk_alert_cooldown_seconds": effective.disk_alert_cooldown_seconds,
    }


@router.put("/system/admin-password")
def update_admin_password(
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
    actor: str = Depends(require_admin),
) -> dict[str, Any]:
    current = str(payload.get("current_password") or "")
    new_password = str(payload.get("new_password") or "")
    if not verify_admin_password(db, settings.admin_password, current):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="当前密码不正确"
        )
    if not MIN_PASSWORD_LENGTH <= len(new_password) <= MAX_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"新密码长度需在 {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} 位之间",
        )
    if current == new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="新密码不能与当前密码相同"
        )

    record = set_admin_password(db, new_password)
    db.add(
        AuditLog(
            actor=actor,
            action="update_admin_password",
            target="admin",
            ts=utcnow(),
            detail=json.dumps({"source": "database"}, ensure_ascii=False),
        )
    )
    db.commit()
    log_event("warn", "auth.admin.password", "管理员密码已更新", actor=actor)
    return {
        "ok": True,
        "password_source": "database",
        "password_updated_at": record.get("updated_at"),
    }


@router.post("/clients/{client_id}/secret/rotate")
def rotate_client_secret(
    client_id: str,
    db: Session = Depends(get_session),
    actor: str = Depends(require_admin),
) -> dict[str, Any]:
    """轮换客户端 secret，明文仅在本次响应中返回一次。"""
    client = _require_client_row(db, client_id)
    secret = new_client_secret()
    client.secret_sha256 = hash_secret(secret)
    rotated_at = utcnow()
    db.add(
        AuditLog(
            actor=actor,
            action="rotate_client_secret",
            target=client_id,
            ts=rotated_at,
            detail=json.dumps({"client_id": client_id}, ensure_ascii=False),
        )
    )
    db.commit()
    log_event(
        "warn", "auth.client.rotate", "客户端 secret 已轮换", client_id=client_id, actor=actor
    )
    return {
        "client_id": client_id,
        "secret": secret,
        "rotated_at": iso_utc(rotated_at),
    }


# --------------------------------------------------------------------------
# 审计日志：查询与导出
# --------------------------------------------------------------------------

AUDIT_EXPORT_LIMIT = 5000


def _epoch_utc(value: float) -> datetime:
    return datetime.fromtimestamp(value, tz=UTC).replace(tzinfo=None)


def _audit_stmt(
    actor: str | None,
    action: str | None,
    target: str | None,
    since: float | None,
    until: float | None,
) -> Select[tuple[AuditLog]]:
    stmt = select(AuditLog)
    if actor:
        stmt = stmt.where(AuditLog.actor.contains(actor.strip()))
    if action:
        stmt = stmt.where(AuditLog.action.contains(action.strip()))
    if target:
        stmt = stmt.where(AuditLog.target.contains(target.strip()))
    if since is not None:
        stmt = stmt.where(AuditLog.ts >= _epoch_utc(since))
    if until is not None:
        stmt = stmt.where(AuditLog.ts <= _epoch_utc(until))
    return stmt


def _serialize_audit(record: AuditLog) -> dict[str, Any]:
    return {
        "id": record.id,
        "actor": record.actor,
        "action": record.action,
        "target": record.target,
        "ts": iso_utc(record.ts),
        "detail": record.detail,
    }


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
) -> dict[str, Any]:
    """审计日志查询：登录、配置变更、手动操作。"""
    stmt = _audit_stmt(actor, action, target, since, until)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(AuditLog.ts.desc(), AuditLog.id.desc()).offset(offset).limit(limit)
    ).all()
    return {"items": [_serialize_audit(record) for record in rows], "total": total}


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
    rows = db.scalars(
        _audit_stmt(actor, action, target, since, until)
        .order_by(AuditLog.ts.asc(), AuditLog.id.asc())
        .limit(AUDIT_EXPORT_LIMIT)
    ).all()
    lines = [json.dumps(_serialize_audit(record), ensure_ascii=False) for record in rows]
    content = "\n".join(lines)
    if content:
        content += "\n"
    filename = "vigil-audit-%s.jsonl" % time.strftime("%Y%m%d-%H%M%S")
    return Response(
        content=content,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="%s"' % filename},
    )


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
) -> list[dict[str, Any]]:
    """服务端结构化日志（合并 api / worker 两个实例）。"""
    if instance and instance not in ("api", "worker"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="instance 必须是 api 或 worker"
        )
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
    if instance and instance not in ("api", "worker"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="instance 必须是 api 或 worker"
        )
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
    return Response(
        content=data,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="%s"' % filename},
    )


# SSE 推送：API 与 worker 是两个进程，状态变更发生在 worker，
# 因此这里按固定间隔轮询数据库版本元组，变化时才通知前端重新拉取 REST 数据。
STREAM_INTERVAL_SECONDS = 3
STREAM_PING_SECONDS = 25
STREAM_RETRY_MS = 5000


def _stream_snapshot() -> dict[str, list[Any]]:
    """读取各业务表的最小版本信息（聚合值，不含具体记录）。"""
    with session_scope() as db:
        incidents = db.execute(
            select(
                func.count(Incident.id),
                func.max(Incident.id),
                func.max(Incident.recovered_at),
                func.max(Incident.acked_at),
                func.max(Incident.notified_at),
            )
        ).one()
        clients = db.execute(select(func.count(Client.id), func.max(Client.created_at))).one()
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
        "incidents": [
            incidents[0],
            incidents[1],
            iso_utc(incidents[2]),
            iso_utc(incidents[3]),
            iso_utc(incidents[4]),
        ],
        "clients": [clients[0], iso_utc(clients[1])],
        "guards": [guards[0], iso_utc(guards[1])],
        "notifications": [notifications[0], notifications[1], iso_utc(notifications[2])],
    }


def _sse_frame(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _admin_event_stream(request: Request, *, once: bool) -> AsyncIterator[str]:
    yield f"retry: {STREAM_RETRY_MS}\n\n"

    try:
        previous = _stream_snapshot()
    except Exception as exc:  # 首次读取失败不中断连接，下一轮再试
        log_event("warn", "admin.stream.snapshot", "读取推送版本失败", error=str(exc))
        previous = {}
    yield _sse_frame(
        "ready", {"version": previous, "server_time": int(time.time())}
    )
    if once:
        return

    last_ping = time.monotonic()
    while True:
        await asyncio.sleep(STREAM_INTERVAL_SECONDS)
        if await request.is_disconnected():
            return
        try:
            current = _stream_snapshot()
        except Exception as exc:  # 数据库短暂锁定时保持连接，避免前端反复重连
            log_event("warn", "admin.stream.snapshot", "读取推送版本失败", error=str(exc))
            current = previous
        if current != previous:
            changed = sorted(key for key in current if current.get(key) != previous.get(key))
            previous = current
            yield _sse_frame(
                "update",
                {
                    "changed": changed,
                    "version": current,
                    "server_time": int(time.time()),
                },
            )
            last_ping = time.monotonic()
        elif time.monotonic() - last_ping >= STREAM_PING_SECONDS:
            last_ping = time.monotonic()
            yield ": ping\n\n"


@router.get("/stream")
async def admin_stream(
    request: Request,
    once: bool = Query(default=False, description="仅输出首帧后关闭，用于调试与探活"),
    _actor: str = Depends(require_admin),
) -> StreamingResponse:
    """管理端 SSE：数据版本变化时推送 update 事件，前端据此重新拉取 REST 数据。"""
    return StreamingResponse(
        _admin_event_stream(request, once=once),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
