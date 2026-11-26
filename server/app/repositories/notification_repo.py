"""通知记录与发送上下文的数据访问。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ClientEvent, Heartbeat, Incident, Notification


def add(
    db: Session,
    *,
    incident_id: int | None,
    client_id: str,
    channel: str,
    target: str,
    kind: str,
    status: str,
    created_at: datetime,
    attempts: int = 0,
    last_error: str | None = None,
    next_attempt_at: datetime | None = None,
    sent_at: datetime | None = None,
) -> Notification:
    row = Notification(
        incident_id=incident_id,
        client_id=client_id,
        channel=channel,
        target=target,
        kind=kind,
        status=status,
        attempts=attempts,
        last_error=last_error,
        next_attempt_at=next_attempt_at,
        created_at=created_at,
        sent_at=sent_at,
    )
    db.add(row)
    return row


def recent(db: Session, limit: int = 20) -> list[Notification]:
    return list(
        db.scalars(select(Notification).order_by(Notification.id.desc()).limit(limit)).all()
    )


def count_sent_since(db: Session, client_id: str, since: datetime) -> int:
    """统计窗口内已发送的非测试通知数，用于限流。"""
    return (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.client_id == client_id,
                Notification.status == "sent",
                Notification.kind != "test",
                Notification.sent_at.is_not(None),
                Notification.sent_at >= since,
            )
        )
        or 0
    )


def count_sent_for_incident(db: Session, incident_id: int, kind: str) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.incident_id == incident_id,
                Notification.kind == kind,
                Notification.status == "sent",
            )
        )
        or 0
    )


def has_incomplete(
    db: Session,
    incident_id: int,
    kind: str,
    statuses: tuple[str, ...] = ("pending", "failed", "skipped", "dead"),
) -> bool:
    """已有未完成的记录时不再重复新建：失败记录交给重试，skipped/dead 避免重复刷表。"""
    count = (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.incident_id == incident_id,
                Notification.kind == kind,
                Notification.status.in_(statuses),
            )
        )
        or 0
    )
    return count > 0


def list_failed_due(
    db: Session, now: datetime, *, max_attempts: int, limit: int = 50
) -> list[Notification]:
    return list(
        db.scalars(
            select(Notification)
            .where(
                Notification.status == "failed",
                Notification.attempts < max_attempts,
                Notification.next_attempt_at.is_not(None),
                Notification.next_attempt_at <= now,
            )
            .order_by(Notification.id.asc())
            .limit(limit)
        ).all()
    )


def pending_recovery_incidents(
    db: Session, since: datetime, limit: int = 50
) -> list[Incident]:
    """查询需要补发恢复通知的 incident。

    恢复由心跳接口写入，不属于 worker 的静默扫描结果，因此需要单独轮询；
    发送前会做「已发送过恢复」去重，重复查询是安全的。
    """
    return list(
        db.scalars(
            select(Incident)
            .where(
                Incident.state == "RECOVERED",
                Incident.notified_at.is_not(None),
                Incident.recovered_at.is_not(None),
                Incident.recovered_at >= since,
            )
            .order_by(Incident.recovered_at)
            .limit(limit)
        )
    )


def latest_heartbeat(db: Session, client_id: str) -> Heartbeat | None:
    stmt = (
        select(Heartbeat)
        .where(Heartbeat.client_id == client_id)
        .order_by(Heartbeat.received_at.desc(), Heartbeat.id.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def baseline_heartbeat(db: Session, incident: Incident) -> Heartbeat | None:
    """故障发生前最后一次心跳，用于对比 boot_id 与 WAN IP。"""
    stmt = (
        select(Heartbeat)
        .where(
            Heartbeat.client_id == incident.client_id,
            Heartbeat.received_at <= incident.started_at,
        )
        .order_by(Heartbeat.received_at.desc(), Heartbeat.id.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def action_events(db: Session, incident: Incident, limit: int) -> list[ClientEvent]:
    """故障期间客户端上报的动作结果，用于恢复通知的动作链。"""
    stmt = select(ClientEvent).where(
        ClientEvent.client_id == incident.client_id,
        ClientEvent.type == "action_result",
        ClientEvent.received_at >= incident.started_at,
    )
    if incident.guard_id:
        stmt = stmt.where(ClientEvent.domain == incident.guard_id)
    return list(
        db.scalars(
            stmt.order_by(ClientEvent.received_at.asc(), ClientEvent.id.asc()).limit(limit)
        ).all()
    )
