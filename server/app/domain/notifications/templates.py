"""通知消息模板：把告警数据渲染为各渠道共用的标题与正文。

本模块只做格式化，不访问数据库；发送上下文由服务层组装后传入。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.domain.notifications.config import CHANNEL_LABELS
from app.domain.notifications.ports import NotificationMessage
from app.logstore import _sanitize, _truncate

if TYPE_CHECKING:
    from app.models import Client, Incident
    from app.state_machine import FlappingInfo

KIND_FLAPPING = "flapping"
GUARD_BAD_STATUSES = ("warn", "critical", "unknown")
MAX_ACTIONS = 10
MAX_SNIPPET = 4096


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


def load_json_object(value: str | None) -> dict[str, Any]:
    try:
        data = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def failed_checks(guard: dict[str, Any]) -> list[str]:
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


def evidence_snippet(guard: dict[str, Any], actions: list[dict[str, Any]]) -> str:
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
    return _truncate(json.dumps(_sanitize(parts), ensure_ascii=False), MAX_SNIPPET)


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


def build_flapping_message(
    incident: Incident,
    info: FlappingInfo,
    client: Client | None,
    channel: str,
) -> NotificationMessage:
    """抖动汇总：说明窗口内的恢复次数与抑制策略。"""
    name = _client_name(client, incident)
    guard_name = incident.guard_name or incident.guard_id or ""
    lines = [f"客户端：{name}（{incident.client_id}）"]
    if guard_name:
        lines.append(f"守护项：{guard_name}")
    lines.extend(
        [
            f"统计窗口：最近 {max(1, info.window_seconds // 60)} 分钟",
            f"恢复次数：{info.count} 次（阈值 {info.threshold} 次）",
            f"最近故障：{_fmt_time(info.latest_down_at)}",
            f"最近恢复：{_fmt_time(info.latest_recovered_at)}",
            "处理方式：窗口内单次告警已抑制，待状态稳定后恢复逐条通知",
        ]
    )
    title = (
        f"[Vigil] 抖动汇总：{name} / {guard_name}"
        if guard_name
        else f"[Vigil] 抖动汇总：{name}"
    )
    return NotificationMessage(
        title=title,
        body="\n".join(lines),
        level="warn",
        incident_id=None,
        metadata={"kind": KIND_FLAPPING, "channel": channel},
    )
