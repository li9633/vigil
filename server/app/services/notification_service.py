"""通知用例：配置读写、发送编排、重试与死信处理。

事务约定：本模块只 `flush` 不 `commit`，由调用方决定事务边界
（worker 的 `session_scope` 或管理端请求）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.notifiers.factory import build_notifiers, target_for
from app.db import utcnow
from app.domain import incident_cause
from app.domain.notifications import config as config_rules
from app.domain.notifications import templates
from app.domain.notifications.ports import NotificationMessage, Notifier, SendResult
from app.errors import ValidationError
from app.logstore import log_event
from app.models import Client, Incident, Notification
from app.repositories import client_repo, notification_repo, setting_repo
from app.state_machine import FlappingInfo, flapping_info

SETTING_KEY = config_rules.SETTING_KEY
SYSTEM_CLIENT_ID = "system"
FLAPPING_SUMMARY_KEY = "flapping_summary"
KIND_FLAPPING = templates.KIND_FLAPPING
KIND_SYSTEM = "system"
MAX_RETRY_ATTEMPTS = 3
RETRY_BASE_SECONDS = 60
DEAD_LETTER_KINDS = ("down", "recovery", "test")
MAX_DEAD_ALERT_ITEMS = 10

mask_config = config_rules.mask_config
validate_config = config_rules.validate_config


# --------------------------------------------------------------------------
# 配置读写
# --------------------------------------------------------------------------


def load_config(db: Session) -> dict[str, Any]:
    raw = setting_repo.get_json(db, SETTING_KEY)
    if raw is None:
        return config_rules.config_from_env()
    return config_rules.normalize_config(raw)


def save_config(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    current = load_config(db)
    merged = config_rules.apply_masked(payload, current)
    try:
        config_rules.validate_config(merged)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    setting_repo.set_json(db, SETTING_KEY, merged)
    return merged


# --------------------------------------------------------------------------
# 发送上下文
# --------------------------------------------------------------------------


def _message_context(
    db: Session, incident: Incident, client: Client | None
) -> dict[str, Any]:
    """汇总通知所需的证据，避免模板依赖数据库。"""
    evidence = templates.load_json_object(incident.evidence_json)
    raw_guard = evidence.get("guard")
    guard: dict[str, Any] = raw_guard if isinstance(raw_guard, dict) else {}

    events = [
        {
            "type": row.type,
            "domain": row.domain,
            "payload": templates.load_json_object(row.payload_json),
        }
        for row in notification_repo.incident_events(db, incident)
    ]

    latest = notification_repo.latest_heartbeat(db, incident.client_id)
    latest_guard: dict[str, Any] = {}
    if incident.guard_id and latest is not None:
        candidate = templates.load_json_object(latest.guards_json).get(incident.guard_id)
        if isinstance(candidate, dict):
            latest_guard = candidate

    baseline = notification_repo.baseline_heartbeat(db, incident)
    start_boot = (baseline.boot_id if baseline else "") or ""
    current_boot = (client.boot_id if client else None) or ""
    start_wan = (baseline.wan_ip if baseline else None) or ""
    current_wan = (client.wan_ip if client else None) or ""
    boot_changed = bool(start_boot and current_boot and start_boot != current_boot)
    wan_ip_changed = bool(start_wan and current_wan and start_wan != current_wan)

    cause = incident_cause.assess_cause(
        category=incident.category,
        boot_changed=boot_changed,
        wan_ip_changed=wan_ip_changed,
        events=events,
        current_uptime=latest.uptime if latest is not None else None,
    )

    recovery_id = cause.recovery_id or str(evidence.get("recovery_id") or "")
    if not recovery_id and incident.guard_id:
        # 守护项告警：最新心跳里的 recovery_id 与该 Guard 一一对应
        recovery_id = str(latest_guard.get("last_recovery_id") or "")

    actions = _recent_actions(events, incident.guard_id)

    return {
        "guard": guard,
        "failed_checks": templates.failed_checks(guard),
        "actions": actions,
        "recovery_id": recovery_id,
        "recovered_by": str(evidence.get("recovered_by") or ""),
        "boot_id_changed": boot_changed,
        "wan_ip_changed": wan_ip_changed,
        "previous_wan_ip": start_wan,
        "current_wan_ip": current_wan,
        "cause_reason": cause.reason,
        "cause_evidence": list(cause.evidence),
        "self_heal_text": cause.self_heal_text,
        "snippet": templates.evidence_snippet(guard, actions),
    }


def _recent_actions(
    events: list[dict[str, Any]], guard_id: str | None
) -> list[dict[str, Any]]:
    """只保留真正执行过的动作；被冷却/限流拒绝的记录已在自愈情况里汇总。"""
    actions: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != "action_result":
            continue
        if guard_id and event.get("domain") != guard_id:
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        result = str(payload.get("result") or "")
        if result not in incident_cause.EXECUTED_RESULTS:
            continue
        actions.append(
            {
                "action": str(payload.get("action") or "-"),
                "target": str(payload.get("target") or ""),
                "result": result,
                "exit_code": payload.get("exit_code"),
                "latency_ms": payload.get("latency_ms"),
                "output": payload.get("output"),
            }
        )
        if len(actions) >= templates.MAX_ACTIONS:
            break
    return actions


# --------------------------------------------------------------------------
# 发送与重试
# --------------------------------------------------------------------------


def _safe_send(notifier: Notifier, message: NotificationMessage) -> SendResult:
    try:
        return notifier.send(message)
    except Exception as exc:  # noqa: BLE001 - 单渠道异常不能影响其他渠道
        return SendResult(False, f"{type(exc).__name__}: 发送异常")


def _rate_limited(db: Session, client_id: str, now: datetime, limit: int) -> bool:
    if limit <= 0:
        return True
    return notification_repo.count_sent_since(db, client_id, now - timedelta(hours=1)) >= limit


def _recovery_already_sent(db: Session, incident: Incident) -> bool:
    return notification_repo.count_sent_for_incident(db, incident.id, "recovery") > 0


def _has_pending_record(db: Session, incident: Incident, kind: str) -> bool:
    return notification_repo.has_incomplete(db, incident.id, kind)


def pending_recovery_incidents(
    db: Session, since: datetime, limit: int = 50
) -> list[Incident]:
    return notification_repo.pending_recovery_incidents(db, since, limit)


def _flapping_scope(client_id: str, guard_id: str | None) -> str:
    return f"{client_id}:{guard_id or '-'}"


def _load_flapping_summary_state(db: Session) -> dict[str, str]:
    data = setting_repo.get_json(db, FLAPPING_SUMMARY_KEY) or {}
    return {str(key): str(value) for key, value in data.items()}


def _summary_sent_recently(
    scope: str, state: dict[str, str], now: datetime, window_seconds: int
) -> bool:
    raw = state.get(scope)
    if not raw:
        return False
    try:
        last_sent = datetime.fromisoformat(raw)
    except ValueError:
        return False
    return (now - last_sent).total_seconds() < window_seconds


def _mark_flapping_summary(db: Session, scope: str, now: datetime) -> None:
    state = _load_flapping_summary_state(db)
    state[scope] = now.isoformat()
    setting_repo.set_json(db, FLAPPING_SUMMARY_KEY, state)


def _send_flapping_summary(
    db: Session,
    incident: Incident,
    info: FlappingInfo,
    now: datetime,
    resolved: dict[str, Any],
    notifiers: list[Notifier],
) -> list[Notification]:
    """每个作用域在每个窗口内只发一条汇总，状态持久化到 Setting 以免重启重复。"""
    scope = _flapping_scope(incident.client_id, incident.guard_id)
    if _summary_sent_recently(
        scope, _load_flapping_summary_state(db), now, info.window_seconds
    ):
        return []

    client = client_repo.get_client(db, incident.client_id)
    records: list[Notification] = []
    for notifier in notifiers:
        record = notification_repo.add(
            db,
            incident_id=None,
            client_id=incident.client_id,
            channel=notifier.channel,
            target=target_for(notifier.channel, resolved),
            kind=KIND_FLAPPING,
            status="pending",
            created_at=now,
        )
        result = _safe_send(
            notifier,
            templates.build_flapping_message(incident, info, client, notifier.channel),
        )
        record.attempts = 1
        if result.ok:
            record.status = "sent"
            record.sent_at = now
        else:
            # 系统/汇总通知不带 incident，无法重建消息重试，直接转入死信供排查
            record.status = "dead"
            record.last_error = result.error[:500]
        records.append(record)

    _mark_flapping_summary(db, scope, now)
    db.flush()
    return records


def send_incident(
    db: Session,
    incident: Incident,
    kind: str,
    now: datetime,
    config: dict[str, Any] | None = None,
    *,
    flapping_window_seconds: int = 1800,
    flapping_threshold: int = 3,
) -> list[Notification]:
    """按渠道发送 DOWN / RECOVERY 通知并写记录；抖动期只发一条汇总。"""
    if kind == "down":
        if incident.state != "DOWN" or incident.notified_at is not None:
            return []
    elif kind == "recovery":
        if incident.state != "RECOVERED" or incident.notified_at is None:
            return []
        if _recovery_already_sent(db, incident):
            return []
    else:
        raise ValueError(f"未知通知类型：{kind}")

    if _has_pending_record(db, incident, kind):
        return []

    resolved = config if config is not None else load_config(db)
    notifiers = build_notifiers(resolved)
    if not notifiers:
        return []

    flapping = flapping_info(
        db,
        incident.client_id,
        incident.guard_id,
        now,
        flapping_window_seconds,
        flapping_threshold,
    )
    if flapping.is_flapping:
        records: list[Notification] = []
        # 恢复通知落 skipped 记录，避免窗口结束后补发过期的「已恢复」
        if kind == "recovery":
            for notifier in notifiers:
                record = notification_repo.add(
                    db,
                    incident_id=incident.id,
                    client_id=incident.client_id,
                    channel=notifier.channel,
                    target=target_for(notifier.channel, resolved),
                    kind=kind,
                    status="skipped",
                    created_at=now,
                )
                record.last_error = "flapping_suppressed"
                records.append(record)
        _send_flapping_summary(db, incident, flapping, now, resolved, notifiers)
        db.flush()
        return records

    client = client_repo.get_client(db, incident.client_id)
    context = _message_context(db, incident, client)
    records = []
    rate_limited = _rate_limited(
        db, incident.client_id, now, int(resolved.get("rate_limit_per_hour", 10))
    )

    for notifier in notifiers:
        record = notification_repo.add(
            db,
            incident_id=incident.id,
            client_id=incident.client_id,
            channel=notifier.channel,
            target=target_for(notifier.channel, resolved),
            kind=kind,
            status="skipped" if rate_limited else "pending",
            created_at=now,
        )
        if rate_limited:
            record.last_error = "rate_limited"
            records.append(record)
            continue

        message = templates.build_message(
            incident, kind, now, client, notifier.channel, context=context
        )
        result = _safe_send(notifier, message)
        record.attempts = 1
        if result.ok:
            record.status = "sent"
            record.sent_at = now
            record.last_error = None
        else:
            record.status = "failed"
            record.last_error = result.error[:500]
            record.next_attempt_at = now + timedelta(seconds=RETRY_BASE_SECONDS)
        records.append(record)

    db.flush()

    if kind == "down" and any(record.status == "sent" for record in records):
        incident.notified_at = now
        db.flush()

    return records


def send_system_alert(
    db: Session,
    now: datetime,
    title: str,
    body: str,
    *,
    level: str = "critical",
    config: dict[str, Any] | None = None,
) -> list[Notification]:
    """发送与具体客户端无关的系统级通知（如磁盘水位），并落库便于审计。"""
    resolved = config if config is not None else load_config(db)
    notifiers = build_notifiers(resolved)
    if not notifiers:
        return []

    message = NotificationMessage(
        title=title,
        body=body,
        level=level,
        incident_id=None,
        metadata={"kind": KIND_SYSTEM},
    )
    records: list[Notification] = []
    for notifier in notifiers:
        record = notification_repo.add(
            db,
            incident_id=None,
            client_id=SYSTEM_CLIENT_ID,
            channel=notifier.channel,
            target=target_for(notifier.channel, resolved),
            kind=KIND_SYSTEM,
            status="pending",
            created_at=now,
        )

        result = _safe_send(notifier, message)
        record.attempts = 1
        if result.ok:
            record.status = "sent"
            record.sent_at = now
        else:
            # 系统/汇总通知不带 incident，无法重建消息重试，直接转入死信供排查
            record.status = "dead"
            record.last_error = result.error[:500]
        records.append(record)

    db.flush()
    return records


def _notify_dead_letters(
    db: Session,
    now: datetime,
    records: list[Notification],
    resolved: dict[str, Any],
) -> None:
    """对转入死信的告警改用剩余渠道提醒管理员，避免渠道故障时静默丢失。"""
    alerts = [record for record in records if record.kind in DEAD_LETTER_KINDS]
    if not alerts:
        return

    failed_channels = sorted({record.channel for record in alerts})
    log_event(
        "error",
        "notify.dead",
        "通知重试耗尽转入死信",
        count=len(alerts),
        channels=failed_channels,
    )
    notifiers = [
        notifier
        for notifier in build_notifiers(resolved)
        if notifier.channel not in failed_channels
    ]
    if not notifiers:
        return

    lines = [f"以下通知重试 {MAX_RETRY_ATTEMPTS} 次后仍失败，已转入死信："]
    for record in alerts[:MAX_DEAD_ALERT_ITEMS]:
        channel = config_rules.CHANNEL_LABELS.get(record.channel, record.channel)
        lines.append(
            f"- {channel} / {record.kind} / 客户端 {record.client_id or '-'} / "
            f"记录 {record.id}：{record.last_error or '-'}"
        )
    if len(alerts) > MAX_DEAD_ALERT_ITEMS:
        lines.append(f"- 另有 {len(alerts) - MAX_DEAD_ALERT_ITEMS} 条未列出")
    lines.append("请检查通知渠道配置与网络连通性，死信记录可在通知记录中查看。")

    message = NotificationMessage(
        title="[Vigil] 通知重试耗尽，已转入死信",
        body="\n".join(lines),
        level="critical",
        incident_id=None,
        metadata={"kind": KIND_SYSTEM},
    )
    for notifier in notifiers:
        result = _safe_send(notifier, message)
        notification_repo.add(
            db,
            incident_id=None,
            client_id=SYSTEM_CLIENT_ID,
            channel=notifier.channel,
            target=target_for(notifier.channel, resolved),
            kind=KIND_SYSTEM,
            status="sent" if result.ok else "dead",
            attempts=1,
            last_error=None if result.ok else result.error[:500],
            created_at=now,
            sent_at=now if result.ok else None,
        )
    db.flush()


def retry_failed(
    db: Session,
    now: datetime,
    config: dict[str, Any] | None = None,
) -> list[Notification]:
    """重发失败记录，最多 3 次；耗尽后转入死信并提醒管理员。"""
    rows = notification_repo.list_failed_due(
        db, now, max_attempts=MAX_RETRY_ATTEMPTS
    )
    if not rows:
        return []

    resolved = config if config is not None else load_config(db)
    notifier_map = {notifier.channel: notifier for notifier in build_notifiers(resolved)}
    clients: dict[str, Client | None] = {}
    touched: list[Notification] = []
    newly_dead: list[Notification] = []

    for record in rows:
        notifier = notifier_map.get(record.channel)
        if notifier is None:
            # 渠道已被移除，无法继续重试
            record.status = "dead"
            record.next_attempt_at = None
            newly_dead.append(record)
            touched.append(record)
            continue

        if record.kind == "test":
            message = templates.build_message(None, "test", now, None, record.channel)
            incident: Incident | None = None
        else:
            incident = db.get(Incident, record.incident_id) if record.incident_id else None
            if incident is None:
                # incident 不存在或记录类型无法重建消息，转入死信供排查
                record.status = "dead"
                record.next_attempt_at = None
                newly_dead.append(record)
                touched.append(record)
                continue
            if record.client_id not in clients:
                clients[record.client_id] = client_repo.get_client(db, record.client_id)
            message = templates.build_message(
                incident,
                record.kind,
                now,
                clients[record.client_id],
                record.channel,
                context=_message_context(db, incident, clients[record.client_id]),
            )

        result = _safe_send(notifier, message)
        record.attempts += 1
        if result.ok:
            record.status = "sent"
            record.sent_at = now
            record.last_error = None
            record.next_attempt_at = None
            if (
                incident is not None
                and record.kind == "down"
                and incident.notified_at is None
            ):
                incident.notified_at = now
        elif record.attempts >= MAX_RETRY_ATTEMPTS:
            record.status = "dead"
            record.last_error = result.error[:500]
            record.next_attempt_at = None
            newly_dead.append(record)
        else:
            record.status = "failed"
            record.last_error = result.error[:500]
            record.next_attempt_at = now + timedelta(
                seconds=RETRY_BASE_SECONDS * record.attempts
            )
        touched.append(record)

    if newly_dead:
        _notify_dead_letters(db, now, newly_dead, resolved)
    db.flush()
    return touched


# --------------------------------------------------------------------------
# 管理端用例
# --------------------------------------------------------------------------


def list_recent_records(db: Session, limit: int = 100) -> list[Notification]:
    return notification_repo.recent(db, limit)


def send_test_notifications(db: Session, channel: str) -> list[dict[str, Any]]:
    """发送测试通知并落库；未启用任何渠道或配置非法时抛 ValidationError。"""
    config = load_config(db)
    try:
        config_rules.validate_config(config)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    notifiers = build_notifiers(config)
    selected = (
        notifiers
        if channel == "all"
        else [notifier for notifier in notifiers if notifier.channel == channel]
    )
    if not selected:
        detail = (
            "没有已启用的通知渠道"
            if channel == "all"
            else f"渠道 {channel} 未配置或未启用"
        )
        raise ValidationError(detail)

    now = utcnow()
    results: list[dict[str, Any]] = []
    for notifier in selected:
        message = templates.build_message(None, "test", now, None, notifier.channel)
        result = _safe_send(notifier, message)
        notification_repo.add(
            db,
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
        results.append(
            {"channel": notifier.channel, "ok": result.ok, "error": result.error}
        )

    db.flush()
    return results
