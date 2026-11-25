"""管理端 API（M3 最小实现）：登录、总览、客户端、案卷、确认。"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session, utcnow
from app.models import AuditLog, Client, ClientEvent, Heartbeat, Incident, Notification
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
from app.schemas import LoginRequest, NotificationTestRequest, iso_utc
from app.security import constant_time_equals
from app.settings import Settings, settings_from_app

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def require_admin(request: Request) -> str:
    actor = request.session.get("admin")
    if not actor:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录",
        )
    return str(actor)


def _client_state(online: bool) -> str:
    """列表页使用的简单派生状态（完整归因由状态机维护）。"""
    return "HEALTHY" if online else "DOWN"


def _serialize_client(
    client: Client,
    settings: Settings,
    now: datetime,
    last_incident: dict[str, Any] | None = None,
) -> dict[str, Any]:
    online = (
        client.last_seen is not None
        and (now - client.last_seen).total_seconds() <= settings.suspect_seconds
    )
    return {
        "client_id": client.client_id,
        "name": client.name,
        "model": client.model,
        "fw_version": client.fw_version,
        "agent_version": client.agent_version,
        "last_seen": iso_utc(client.last_seen),
        "last_beacon": iso_utc(client.last_beacon),
        "boot_id": client.boot_id,
        "wan_ip": client.wan_ip,
        "enabled": client.enabled,
        "online": online,
        "state": _client_state(online),
        "last_incident": last_incident,
        "created_at": iso_utc(client.created_at),
    }


def _serialize_incident(incident: Incident) -> dict[str, Any]:
    try:
        evidence = json.loads(incident.evidence_json or "{}")
    except json.JSONDecodeError:
        evidence = {}
    return {
        "id": incident.id,
        "client_id": incident.client_id,
        "category": incident.category,
        "state": incident.state,
        "started_at": iso_utc(incident.started_at),
        "detected_at": iso_utc(incident.detected_at),
        "recovered_at": iso_utc(incident.recovered_at),
        "acked_at": iso_utc(incident.acked_at),
        "notified_at": iso_utc(incident.notified_at),
        "summary": incident.summary,
        "evidence": evidence,
    }


def _serialize_heartbeat(heartbeat: Heartbeat) -> dict[str, Any]:
    try:
        modules = json.loads(heartbeat.modules_json or "{}")
    except json.JSONDecodeError:
        modules = {}
    received_at = iso_utc(heartbeat.received_at)
    return {
        "received_at": received_at,
        "ts": received_at,
        "seq": heartbeat.seq,
        "uptime": heartbeat.uptime,
        "boot_id": heartbeat.boot_id,
        "wan_ip": heartbeat.wan_ip,
        "modules": modules,
    }


def _serialize_event(event: ClientEvent) -> dict[str, Any]:
    try:
        payload = json.loads(event.payload_json or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {"value": payload}
    return {
        "event_id": event.event_id,
        "client_id": event.client_id,
        "ts": iso_utc(event.received_at),
        "type": event.type,
        "domain": event.domain,
        "module": event.domain or None,
        "level": payload.get("status") or payload.get("level"),
        "message": payload.get("message") or payload.get("reason") or None,
        "payload": payload,
    }


def _modules_from_heartbeat(heartbeat: Heartbeat | None) -> list[dict[str, Any]]:
    """把最近一次心跳的 modules 字典展开成数组（供模块状态表使用）。"""
    if heartbeat is None:
        return []
    try:
        modules = json.loads(heartbeat.modules_json or "{}")
    except json.JSONDecodeError:
        return []
    if not isinstance(modules, dict):
        return []
    items: list[dict[str, Any]] = []
    for module_id, value in modules.items():
        if not isinstance(value, dict):
            continue
        items.append(
            {
                "id": str(module_id),
                "status": value.get("status"),
                "latency_ms": value.get("latency_ms"),
                "uptime": value.get("uptime"),
            }
        )
    return items


def _latest_incident_map(db: Session, client_ids: list[str]) -> dict[str, dict[str, Any]]:
    """为客户端列表批量取每个客户端最近一条 incident（已序列化）。"""
    if not client_ids:
        return {}
    rows = db.scalars(
        select(Incident)
        .where(Incident.client_id.in_(client_ids))
        .order_by(Incident.detected_at.desc())
    ).all()
    latest: dict[str, dict[str, Any]] = {}
    for incident in rows:
        if incident.client_id not in latest:
            latest[incident.client_id] = _serialize_incident(incident)
    return latest


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
) -> dict[str, Any]:
    valid = constant_time_equals(payload.username, settings.admin_username) and (
        constant_time_equals(payload.password, settings.admin_password)
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    request.session["admin"] = payload.username
    db.add(
        AuditLog(
            actor=payload.username,
            action="login",
            target="admin",
            ts=utcnow(),
            detail=json.dumps(
                {"ip": request.client.host if request.client else ""}, ensure_ascii=False
            ),
        )
    )
    db.commit()
    return {"ok": True, "actor": payload.username, "server_time": int(time.time())}


@router.get("/overview")
def overview(
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    now = utcnow()
    online_since = now - timedelta(seconds=settings.suspect_seconds)
    clients_total = db.scalar(select(func.count()).select_from(Client)) or 0
    clients_online = (
        db.scalar(
            select(func.count())
            .select_from(Client)
            .where(Client.last_seen.is_not(None), Client.last_seen >= online_since)
        )
        or 0
    )
    incidents_open = (
        db.scalar(
            select(func.count()).select_from(Incident).where(Incident.recovered_at.is_(None))
        )
        or 0
    )
    incidents_24h = (
        db.scalar(
            select(func.count())
            .select_from(Incident)
            .where(Incident.detected_at >= now - timedelta(hours=24))
        )
        or 0
    )
    timeline_rows = db.scalars(
        select(Incident).order_by(Incident.detected_at.desc()).limit(20)
    ).all()
    timeline = [
        {
            "id": incident.id,
            "ts": iso_utc(incident.detected_at),
            "type": incident.category,
            "severity": incident.state,
            "client_id": incident.client_id,
            "summary": incident.summary,
        }
        for incident in timeline_rows
    ]
    return {
        "clients_total": clients_total,
        "clients_online": clients_online,
        "clients_down": max(0, clients_total - clients_online),
        "incidents_open": incidents_open,
        "open_incidents": incidents_open,
        "incidents_24h": incidents_24h,
        "flapping_clients": 0,
        "notification_success_rate": None,
        "timeline": timeline,
        "generated_at": iso_utc(now),
        "server_time": int(time.time()),
    }


@router.get("/clients")
def list_clients(
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    now = utcnow()
    clients = db.scalars(select(Client).order_by(Client.created_at.desc())).all()
    latest = _latest_incident_map(db, [client.client_id for client in clients])
    return {
        "items": [
            _serialize_client(client, settings, now, latest.get(client.client_id))
            for client in clients
        ]
    }


@router.get("/clients/{client_id}")
def get_client_detail(
    client_id: str,
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    client = db.scalars(select(Client).where(Client.client_id == client_id)).one_or_none()
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="客户端不存在")

    now = utcnow()
    heartbeats = db.scalars(
        select(Heartbeat)
        .where(Heartbeat.client_id == client_id)
        .order_by(Heartbeat.received_at.desc())
        .limit(100)
    ).all()
    incidents = db.scalars(
        select(Incident)
        .where(Incident.client_id == client_id)
        .order_by(Incident.detected_at.desc())
        .limit(100)
    ).all()
    events = db.scalars(
        select(ClientEvent)
        .where(ClientEvent.client_id == client_id)
        .order_by(ClientEvent.received_at.desc())
        .limit(100)
    ).all()
    return {
        "client": _serialize_client(client, settings, now, _latest_incident_map(db, [client_id]).get(client_id)),
        "modules": _modules_from_heartbeat(heartbeats[0] if heartbeats else None),
        "heartbeats": [_serialize_heartbeat(hb) for hb in heartbeats],
        "incidents": [_serialize_incident(incident) for incident in incidents],
        "recent_events": [_serialize_event(event) for event in events],
        "actions": [],
        "generated_at": iso_utc(now),
    }


@router.get("/incidents")
def list_incidents(
    client_id: str | None = Query(default=None),
    state: str | None = Query(default=None),
    category: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    stmt = select(Incident)
    if client_id:
        stmt = stmt.where(Incident.client_id == client_id)
    if state:
        stmt = stmt.where(Incident.state == state)
    if category:
        stmt = stmt.where(Incident.category == category)
    incidents = db.scalars(stmt.order_by(Incident.detected_at.desc()).limit(limit)).all()
    return {"items": [_serialize_incident(incident) for incident in incidents]}


@router.post("/incidents/{incident_id}/ack")
def ack_incident(
    incident_id: int,
    db: Session = Depends(get_session),
    actor: str = Depends(require_admin),
) -> dict[str, Any]:
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
    return _serialize_incident(incident)


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
