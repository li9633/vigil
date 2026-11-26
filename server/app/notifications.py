"""通知配置存取、消息模板、发送与失败重试。

约定：
- 配置以 JSON 存于 `Setting(key='notifications')`，未保存时回退环境变量
- 每个启用渠道每次发送写一条 `Notification` 记录，单渠道失败不影响其他渠道
- DOWN 通知仅发一次（`incident.notified_at`），恢复通知仅在发过 DOWN 后发送
"""

from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.logstore import _sanitize, _truncate
from app.models import Client, ClientEvent, Heartbeat, Incident, Notification, Setting
from app.notifiers.base import NotificationMessage, Notifier, SendResult
from app.notifiers.factory import build_notifiers, target_for

SETTING_KEY = "notifications"
SYSTEM_CLIENT_ID = "system"
MASK_PREFIX = "****"
MAX_RETRY_ATTEMPTS = 3
RETRY_BASE_SECONDS = 60
SECURITY_MODES = ("ssl", "starttls", "none")

DEFAULT_CONFIG: dict[str, Any] = {
    "feishu": {"enabled": False, "webhook": ""},
    "wecom": {"enabled": False, "webhook": ""},
    "email": {
        "enabled": False,
        "smtp_host": "",
        "smtp_port": 465,
        "username": "",
        "password": "",
        "from": "",
        "to": "",
        "security": "ssl",
    },
    "rate_limit_per_hour": 10,
}

CHANNEL_LABELS = {"feishu": "飞书", "wecom": "企业微信", "email": "邮件"}


# --------------------------------------------------------------------------
# 配置：默认值 / 环境变量 / 存取 / 掩码 / 校验
# --------------------------------------------------------------------------


def default_config() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_CONFIG)


def _env(name: str) -> str:
    value = os.getenv(name)
    return "" if value is None else value.strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


def config_from_env() -> dict[str, Any]:
    """未保存配置时的回退值：仅填写存在环境变量的字段。"""
    config = default_config()

    feishu_webhook = _env("VIGIL_FEISHU_WEBHOOK")
    if feishu_webhook:
        config["feishu"] = {"enabled": True, "webhook": feishu_webhook}

    wecom_webhook = _env("VIGIL_WECOM_WEBHOOK")
    if wecom_webhook:
        config["wecom"] = {"enabled": True, "webhook": wecom_webhook}

    host = _env("VIGIL_SMTP_HOST")
    sender = _env("VIGIL_SMTP_FROM")
    recipients = _env("VIGIL_SMTP_TO")
    security = _env("VIGIL_SMTP_SECURITY") or "ssl"
    if security not in SECURITY_MODES:
        security = "ssl"
    config["email"] = {
        "enabled": bool(host and sender and recipients),
        "smtp_host": host,
        "smtp_port": _env_int("VIGIL_SMTP_PORT", 465),
        "username": _env("VIGIL_SMTP_USERNAME"),
        "password": _env("VIGIL_SMTP_PASSWORD"),
        "from": sender,
        "to": recipients,
        "security": security,
    }
    return config


def _normalize_to(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ",".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def normalize_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """在默认结构上合并，忽略未知字段并做类型收敛。"""
    config = default_config()
    if not isinstance(raw, dict):
        return config

    for channel in ("feishu", "wecom"):
        section = raw.get(channel)
        if isinstance(section, dict):
            config[channel]["enabled"] = bool(section.get("enabled", False))
            config[channel]["webhook"] = str(section.get("webhook") or "").strip()

    email = raw.get("email")
    if isinstance(email, dict):
        target = config["email"]
        target["enabled"] = bool(email.get("enabled", False))
        target["smtp_host"] = str(email.get("smtp_host") or "").strip()
        try:
            target["smtp_port"] = int(email.get("smtp_port") or 465)
        except (TypeError, ValueError):
            target["smtp_port"] = 465
        target["username"] = str(email.get("username") or "").strip()
        target["password"] = str(email.get("password") or "")
        target["from"] = str(email.get("from") or "").strip()
        target["to"] = _normalize_to(email.get("to"))
        security = str(email.get("security") or "ssl").strip()
        target["security"] = security if security in SECURITY_MODES else "ssl"

    try:
        rate_limit = int(raw.get("rate_limit_per_hour", config["rate_limit_per_hour"]))
    except (TypeError, ValueError):
        rate_limit = config["rate_limit_per_hour"]
    config["rate_limit_per_hour"] = rate_limit if rate_limit >= 0 else config["rate_limit_per_hour"]
    return config


def load_config(db: Session) -> dict[str, Any]:
    row = db.get(Setting, SETTING_KEY)
    if row is None or not (row.value or "").strip():
        return config_from_env()
    try:
        raw = json.loads(row.value)
    except json.JSONDecodeError:
        return config_from_env()
    if not isinstance(raw, dict):
        return config_from_env()
    return normalize_config(raw)


def apply_masked(payload: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """以 **** 开头的值表示沿用旧值（前端只展示掩码）。"""
    merged = normalize_config(payload)

    def keep(section_name: str, field: str) -> None:
        incoming = merged[section_name].get(field, "")
        previous = current.get(section_name, {}).get(field, "")
        if isinstance(incoming, str) and incoming.startswith(MASK_PREFIX):
            merged[section_name][field] = previous

    keep("feishu", "webhook")
    keep("wecom", "webhook")
    keep("email", "password")
    return merged


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    text = str(value)
    return f"{MASK_PREFIX}{text[-4:]}"


def mask_config(config: dict[str, Any]) -> dict[str, Any]:
    masked = normalize_config(config)
    masked["feishu"]["webhook"] = mask_secret(masked["feishu"]["webhook"])
    masked["wecom"]["webhook"] = mask_secret(masked["wecom"]["webhook"])
    masked["email"]["password"] = mask_secret(masked["email"]["password"])
    return masked


def _is_http_url(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def validate_config(config: dict[str, Any]) -> None:
    """校验保存/测试前的配置，失败抛 ValueError（由 API 转 400）。"""
    for channel, label in (("feishu", "飞书"), ("wecom", "企业微信")):
        section = config.get(channel) or {}
        webhook = str(section.get("webhook") or "").strip()
        if section.get("enabled") and not webhook:
            raise ValueError(f"{label} Webhook 不能为空")
        if webhook and not _is_http_url(webhook):
            raise ValueError(f"{label} Webhook 必须以 http:// 或 https:// 开头")

    email = config.get("email") or {}
    if email.get("enabled"):
        for field, message in (
            ("smtp_host", "SMTP 主机不能为空"),
            ("from", "发件人不能为空"),
            ("to", "收件人不能为空"),
        ):
            if not str(email.get(field) or "").strip():
                raise ValueError(f"邮件已启用：{message}")
        port = email.get("smtp_port")
        if port is None:
            raise ValueError("SMTP 端口必须是整数")
        try:
            port_number = int(port)
        except (TypeError, ValueError):
            raise ValueError("SMTP 端口必须是整数") from None
        if not 1 <= port_number <= 65535:
            raise ValueError("SMTP 端口需在 1-65535 之间")
    if str(email.get("security") or "ssl") not in SECURITY_MODES:
        raise ValueError("邮件安全模式仅支持 ssl / starttls / none")

    rate_limit = config.get("rate_limit_per_hour")
    if rate_limit is None:
        raise ValueError("每小时通知上限必须是整数")
    try:
        rate_number = int(rate_limit)
    except (TypeError, ValueError):
        raise ValueError("每小时通知上限必须是整数") from None
    if rate_number < 0:
        raise ValueError("每小时通知上限不能为负数")


def save_config(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    current = load_config(db)
    merged = apply_masked(payload, current)
    validate_config(merged)

    row = db.get(Setting, SETTING_KEY)
    if row is None:
        row = Setting(key=SETTING_KEY, value="", updated_at=utcnow())
        db.add(row)
    row.value = json.dumps(merged, ensure_ascii=False)
    row.updated_at = utcnow()
    db.flush()
    return merged


# --------------------------------------------------------------------------
# 消息模板
# --------------------------------------------------------------------------


def _fmt_time(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S UTC") if value else "-"


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分"
    if minutes:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"


def _client_name(client: Client | None, incident: Incident) -> str:
    if client is not None and client.name:
        return client.name
    return incident.client_id


GUARD_BAD_STATUSES = ("warn", "critical", "unknown")
MAX_ACTIONS = 10
MAX_SNIPPET = 4096


def _load_json(value: str | None) -> dict[str, Any]:
    try:
        data = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _latest_heartbeat(db: Session, client_id: str) -> Heartbeat | None:
    stmt = (
        select(Heartbeat)
        .where(Heartbeat.client_id == client_id)
        .order_by(Heartbeat.received_at.desc(), Heartbeat.id.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def _baseline_heartbeat(db: Session, incident: Incident) -> Heartbeat | None:
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


def _failed_checks(guard: dict[str, Any]) -> list[str]:
    checks = guard.get("checks")
    if not isinstance(checks, dict):
        return []
    failed: list[str] = []
    for name, check in checks.items():
        if not isinstance(check, dict):
            continue
        status = str(check.get("status") or "")
        if status not in GUARD_BAD_STATUSES:
            continue
        label = f"{name}（{status}"
        latency = check.get("latency_ms")
        if isinstance(latency, int) and not isinstance(latency, bool):
            label += f"，{latency}ms"
        failed.append(label + "）")
    return failed


def _recent_actions(db: Session, incident: Incident) -> list[dict[str, Any]]:
    """故障期间客户端上报的动作结果，用于恢复通知的动作链。"""
    stmt = select(ClientEvent).where(
        ClientEvent.client_id == incident.client_id,
        ClientEvent.type == "action_result",
        ClientEvent.received_at >= incident.started_at,
    )
    if incident.guard_id:
        stmt = stmt.where(ClientEvent.domain == incident.guard_id)
    rows = db.scalars(
        stmt.order_by(ClientEvent.received_at.asc(), ClientEvent.id.asc()).limit(
            MAX_ACTIONS
        )
    ).all()
    actions: list[dict[str, Any]] = []
    for row in rows:
        payload = _load_json(row.payload_json)
        actions.append(
            {
                "action": str(payload.get("action") or "-"),
                "target": str(payload.get("target") or ""),
                "result": str(payload.get("result") or ""),
                "exit_code": payload.get("exit_code"),
                "latency_ms": payload.get("latency_ms"),
                "output": payload.get("output"),
            }
        )
    return actions


def _evidence_snippet(guard: dict[str, Any], actions: list[dict[str, Any]]) -> str:
    parts: list[dict[str, Any]] = []
    checks = guard.get("checks")
    if isinstance(checks, dict):
        for name, check in checks.items():
            if isinstance(check, dict) and check.get("evidence"):
                parts.append(
                    {
                        "check": name,
                        "status": check.get("status"),
                        "evidence": check.get("evidence"),
                    }
                )
    for action in actions:
        if action.get("output"):
            parts.append({"action": action.get("action"), "output": action.get("output")})
    if not parts:
        return ""
    return _truncate(
        json.dumps(_sanitize(parts), ensure_ascii=False), MAX_SNIPPET
    )


def _message_context(
    db: Session, incident: Incident, client: Client | None
) -> dict[str, Any]:
    """汇总通知所需的证据，避免 build_message 依赖数据库。"""
    evidence = _load_json(incident.evidence_json)
    raw_guard = evidence.get("guard")
    guard: dict[str, Any] = raw_guard if isinstance(raw_guard, dict) else {}

    latest_guard: dict[str, Any] = {}
    if incident.guard_id:
        latest = _latest_heartbeat(db, incident.client_id)
        if latest is not None:
            candidate = _load_json(latest.guards_json).get(incident.guard_id)
            if isinstance(candidate, dict):
                latest_guard = candidate

    recovery_id = ""
    for source in (latest_guard, guard):
        value = str(source.get("last_recovery_id") or "")
        if value:
            recovery_id = value
            break
    if not recovery_id:
        recovery_id = str(evidence.get("recovery_id") or "")

    baseline = _baseline_heartbeat(db, incident)
    start_boot = (baseline.boot_id if baseline else "") or ""
    current_boot = (client.boot_id if client else None) or ""
    start_wan = (baseline.wan_ip if baseline else None) or ""
    current_wan = (client.wan_ip if client else None) or ""
    actions = _recent_actions(db, incident)

    return {
        "guard": guard,
        "failed_checks": _failed_checks(guard),
        "actions": actions,
        "recovery_id": recovery_id,
        "recovered_by": str(evidence.get("recovered_by") or ""),
        "boot_id_changed": bool(start_boot and current_boot and start_boot != current_boot),
        "wan_ip_changed": bool(start_wan and current_wan and start_wan != current_wan),
        "previous_wan_ip": start_wan,
        "current_wan_ip": current_wan,
        "snippet": _evidence_snippet(guard, actions),
    }


def _format_actions(actions: list[dict[str, Any]]) -> list[str]:
    if not actions:
        return ["动作链：无记录"]
    lines = ["动作链："]
    for index, action in enumerate(actions, 1):
        detail = str(action.get("action") or "-")
        if action.get("target"):
            detail += f" → {action['target']}"
        detail += f" {action.get('result') or '-'}"
        if isinstance(action.get("exit_code"), int):
            detail += f"（exit {action['exit_code']}）"
        if isinstance(action.get("latency_ms"), int):
            detail += f"，{action['latency_ms']}ms"
        lines.append(f"  {index}. {detail}")
    return lines


def build_message(
    incident: Incident | None,
    kind: str,
    now: datetime,
    client: Client | None,
    channel: str,
    context: dict[str, Any] | None = None,
) -> NotificationMessage:
    if kind == "test":
        return NotificationMessage(
            title="[Vigil] 通知测试",
            body=f"发送时间：{_fmt_time(now)}\n渠道：{CHANNEL_LABELS.get(channel, channel)}",
            level="info",
            incident_id=None,
            metadata={"kind": "test", "channel": channel},
        )
    if incident is None:
        raise ValueError(f"通知类型 {kind} 缺少 incident")

    ctx = context or {}
    name = _client_name(client, incident)
    guard_name = incident.guard_name or incident.guard_id or ""

    if kind == "down":
        last_seen = client.last_seen if client else None
        missing = (now - last_seen).total_seconds() if last_seen else None
        lines = [f"客户端：{name}（{incident.client_id}）"]
        if guard_name:
            lines.append(f"守护项：{guard_name}")
        lines.extend(
            [
                f"类别：{incident.category}",
                f"首次异常：{_fmt_time(incident.started_at)}",
                f"最后心跳：{_fmt_time(last_seen)}",
                f"WAN IP：{(client.wan_ip if client else None) or '-'}",
                f"缺失时长：{_fmt_duration(missing)}",
            ]
        )
        failed = ctx.get("failed_checks") or []
        if failed:
            lines.append("失败 Check：" + "、".join(failed))
        lines.append("boot_id：" + ("已变化" if ctx.get("boot_id_changed") else "未变化"))
        title = (
            f"[Vigil] 守护项异常：{name} / {guard_name}"
            if guard_name
            else f"[Vigil] 客户端离线：{name}"
        )
        return NotificationMessage(
            title=title,
            body="\n".join(lines),
            level="critical",
            incident_id=incident.id,
            metadata={"kind": kind, "channel": channel},
        )

    if kind == "recovery":
        duration = None
        if incident.recovered_at is not None:
            duration = (incident.recovered_at - incident.started_at).total_seconds()
        lines = [f"客户端：{name}（{incident.client_id}）"]
        if guard_name:
            lines.append(f"守护项：{guard_name}")
        lines.extend(
            [
                f"类别：{incident.category}",
                f"故障开始：{_fmt_time(incident.started_at)}",
                f"恢复时间：{_fmt_time(incident.recovered_at)}",
                f"持续时长：{_fmt_duration(duration)}",
            ]
        )
        recovered_by = (
            "服务端扫描" if ctx.get("recovered_by") == "server_scan" else "客户端心跳"
        )
        lines.append(f"恢复判定方：{recovered_by}")
        lines.extend(_format_actions(ctx.get("actions") or []))
        lines.append("是否重启：" + ("是" if ctx.get("boot_id_changed") else "否"))
        if ctx.get("wan_ip_changed"):
            lines.append(
                f"WAN IP：{ctx.get('previous_wan_ip')} → {ctx.get('current_wan_ip')}（已变化）"
            )
        else:
            lines.append(f"WAN IP：{ctx.get('current_wan_ip') or '-'}（未变化）")
        lines.append(f"recovery_id：{ctx.get('recovery_id') or '-'}")
        snippet = ctx.get("snippet") or ""
        if snippet:
            lines.append(f"证据摘要：{snippet}")
        lines.append(f"摘要：{incident.summary or '-'}")
        title = (
            f"[Vigil] 已恢复：{name} / {guard_name}"
            if guard_name
            else f"[Vigil] 已恢复：{name}"
        )
        return NotificationMessage(
            title=title,
            body="\n".join(lines),
            level="ok",
            incident_id=incident.id,
            metadata={"kind": kind, "channel": channel},
        )

    raise ValueError(f"未知通知类型：{kind}")


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
    since = now - timedelta(hours=1)
    count = (
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
    return count >= limit


def _recovery_already_sent(db: Session, incident: Incident) -> bool:
    count = (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.incident_id == incident.id,
                Notification.kind == "recovery",
                Notification.status == "sent",
            )
        )
        or 0
    )
    return count > 0


def _has_pending_record(db: Session, incident: Incident, kind: str) -> bool:
    """已有未完成的记录时不再重复新建：失败记录交给 retry_failed，skipped 记录避免每轮重复刷表。"""
    count = (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.incident_id == incident.id,
                Notification.kind == kind,
                Notification.status.in_(("pending", "failed", "skipped")),
            )
        )
        or 0
    )
    return count > 0


def pending_recovery_incidents(
    db: Session, since: datetime, limit: int = 50
) -> list[Incident]:
    """查询需要补发恢复通知的 incident。

    恢复由心跳接口写入，不属于 worker 的静默扫描结果，因此需要单独轮询；
    send_incident 内部会做「已发送过恢复」去重，重复查询是安全的。
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


def send_incident(
    db: Session,
    incident: Incident,
    kind: str,
    now: datetime,
    config: dict[str, Any] | None = None,
) -> list[Notification]:
    """按渠道发送 DOWN / RECOVERY 通知并写记录；不满足条件时返回空列表。"""
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

    client = db.scalars(
        select(Client).where(Client.client_id == incident.client_id)
    ).one_or_none()
    context = _message_context(db, incident, client)
    records: list[Notification] = []
    rate_limited = _rate_limited(
        db, incident.client_id, now, int(resolved.get("rate_limit_per_hour", 10))
    )

    for notifier in notifiers:
        record = Notification(
            incident_id=incident.id,
            client_id=incident.client_id,
            channel=notifier.channel,
            target=target_for(notifier.channel, resolved),
            kind=kind,
            status="skipped" if rate_limited else "pending",
            attempts=0,
            created_at=now,
        )
        if rate_limited:
            record.last_error = "rate_limited"
            db.add(record)
            records.append(record)
            continue

        message = build_message(
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
        db.add(record)
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
        metadata={"kind": "system"},
    )
    records: list[Notification] = []
    for notifier in notifiers:
        record = Notification(
            incident_id=None,
            client_id=SYSTEM_CLIENT_ID,
            channel=notifier.channel,
            target=target_for(notifier.channel, resolved),
            kind="system",
            status="pending",
            attempts=0,
            created_at=now,
        )

        result = _safe_send(notifier, message)
        record.attempts = 1
        if result.ok:
            record.status = "sent"
            record.sent_at = now
        else:
            record.status = "failed"
            record.last_error = result.error[:500]
            record.next_attempt_at = now + timedelta(seconds=RETRY_BASE_SECONDS)
        db.add(record)
        records.append(record)

    db.flush()
    return records


def retry_failed(
    db: Session,
    now: datetime,
    config: dict[str, Any] | None = None,
) -> list[Notification]:
    """重发失败记录，最多 3 次；单条异常不影响其他记录。"""
    rows = list(
        db.scalars(
            select(Notification)
            .where(
                Notification.status == "failed",
                Notification.attempts < MAX_RETRY_ATTEMPTS,
                Notification.next_attempt_at.is_not(None),
                Notification.next_attempt_at <= now,
            )
            .order_by(Notification.id.asc())
            .limit(50)
        ).all()
    )
    if not rows:
        return []

    resolved = config if config is not None else load_config(db)
    notifier_map = {notifier.channel: notifier for notifier in build_notifiers(resolved)}
    clients: dict[str, Client | None] = {}
    touched: list[Notification] = []

    for record in rows:
        notifier = notifier_map.get(record.channel)
        if notifier is None:
            continue

        if record.kind == "test":
            message = build_message(None, "test", now, None, record.channel)
            incident: Incident | None = None
        else:
            incident = db.get(Incident, record.incident_id) if record.incident_id else None
            if incident is None:
                continue
            if record.client_id not in clients:
                clients[record.client_id] = db.scalars(
                    select(Client).where(Client.client_id == record.client_id)
                ).one_or_none()
            message = build_message(
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
        else:
            record.status = "failed"
            record.last_error = result.error[:500]
            record.next_attempt_at = (
                now + timedelta(seconds=RETRY_BASE_SECONDS * record.attempts)
                if record.attempts < MAX_RETRY_ATTEMPTS
                else None
            )
        touched.append(record)

    db.flush()
    return touched


def serialize_notification(record: Notification) -> dict[str, Any]:
    def iso(value: datetime | None) -> str | None:
        return None if value is None else value.isoformat() + "Z"

    return {
        "id": record.id,
        "incident_id": record.incident_id,
        "client_id": record.client_id,
        "channel": record.channel,
        "target": record.target,
        "kind": record.kind,
        "status": record.status,
        "attempts": record.attempts,
        "last_error": record.last_error,
        "next_attempt_at": iso(record.next_attempt_at),
        "created_at": iso(record.created_at),
        "sent_at": iso(record.sent_at),
    }
