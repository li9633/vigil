"""故障原因归因：把心跳差异与客户端补传的证据汇总成可读的推断结论。

服务端只是旁路观察者，无法替代现场诊断，因此这里输出的是「推断 + 证据」，
并要求调用方把「故障原因」「自愈情况」「恢复方式」分开呈现，避免把
「设备重启后心跳恢复」误读成故障根因。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

CAUSE_DEVICE_REBOOT_LINK_DOWN = "device_reboot_link_down"
CAUSE_DEVICE_REBOOT = "device_reboot"
CAUSE_WAN_LINK_DOWN = "wan_link_down"
CAUSE_WAN_LINK_UNKNOWN = "wan_link_unknown"
CAUSE_PPPOE_DOWN = "pppoe_down"
CAUSE_UPSTREAM_DOWN = "upstream_down"
CAUSE_AGENT_DOWN = "agent_down"
CAUSE_UNKNOWN = "unknown"

SELF_HEAL_SUCCESS = "success"
SELF_HEAL_FAILED = "failed"
SELF_HEAL_BLOCKED = "blocked"
SELF_HEAL_NONE = "none"

UPSTREAM_CHECKS = ("default-route", "dns", "http204")
EXECUTED_RESULTS = ("success", "failed", "ok")

_REASON_LABELS = {
    "cooldown": "动作冷却中",
    "hourly_limit": "达到每小时动作上限",
    "maintenance": "处于维护窗口",
    "disabled": "自动动作已禁用",
    "level_disabled": "动作等级受限",
    "locked": "并发锁占用",
    "not_whitelisted": "动作不在白名单",
    "target_required": "缺少动作目标",
    "target_invalid": "动作目标非法",
    "target_not_whitelisted": "动作目标不在白名单",
}


@dataclass(frozen=True)
class CauseAssessment:
    """归因结果：原因、证据与自愈执行情况。"""

    code: str
    reason: str
    evidence: tuple[str, ...] = ()
    self_heal: str = SELF_HEAL_NONE
    self_heal_text: str = "无本地自愈动作记录"
    recovery_id: str = ""


@dataclass
class _Signals:
    saw_device_reboot_event: bool = False
    carrier_down: bool = False
    carrier_unknown: bool = False
    carrier_recovered: bool = False
    pppoe_down: bool = False
    pppoe_unknown: bool = False
    pppoe_recovered: bool = False
    upstream_down: list[str] = field(default_factory=list)
    heal_verified: int = 0
    heal_executed_rounds: int = 0
    executed_actions: int = 0
    rejected_actions: int = 0
    rejected_reasons: dict[str, int] = field(default_factory=dict)
    verified_recovery_id: str = ""


def _pay(event: Mapping[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def _evidence_reason(payload: Mapping[str, Any]) -> str:
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        return ""
    reason = evidence.get("reason")
    return str(reason or "")


def _carrier_value(payload: Mapping[str, Any]) -> str:
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        return ""
    observed = evidence.get("observed")
    if not isinstance(observed, dict):
        return ""
    return str(observed.get("carrier") or "")


def _collect(events: Iterable[Mapping[str, Any]]) -> _Signals:
    signals = _Signals()
    for event in events:
        event_type = str(event.get("type") or "")
        payload = _pay(event)
        if event_type == "device_reboot":
            signals.saw_device_reboot_event = True
        elif event_type == "check_status":
            check_id = str(payload.get("check_id") or event.get("domain") or "")
            status = str(payload.get("status") or "")
            reason = _evidence_reason(payload)
            if check_id == "wan-link":
                if status == "ok":
                    signals.carrier_recovered = True
                elif reason == "carrier_down" or _carrier_value(payload) == "0":
                    signals.carrier_down = True
                elif status == "critical":
                    signals.carrier_unknown = True
            elif check_id == "pppoe-session":
                if status == "ok":
                    signals.pppoe_recovered = True
                elif status == "critical":
                    if reason == "pppoe_unknown":
                        signals.pppoe_unknown = True
                    else:
                        signals.pppoe_down = True
            elif check_id in UPSTREAM_CHECKS and status == "critical":
                if check_id not in signals.upstream_down:
                    signals.upstream_down.append(check_id)
        elif event_type == "guard_recovery":
            recovery_id = str(payload.get("recovery_id") or "")
            if payload.get("recovered"):
                signals.heal_verified += 1
                if recovery_id:
                    signals.verified_recovery_id = recovery_id
            elif not payload.get("skipped"):
                signals.heal_executed_rounds += 1
        elif event_type == "action_result":
            result = str(payload.get("result") or "")
            if result in EXECUTED_RESULTS:
                signals.executed_actions += 1
            elif result == "rejected":
                signals.rejected_actions += 1
                reason = str(payload.get("reason") or "rejected")
                signals.rejected_reasons[reason] = (
                    signals.rejected_reasons.get(reason, 0) + 1
                )
    return signals


def _self_heal_text(signals: _Signals) -> tuple[str, str]:
    blocked = sorted(
        signals.rejected_reasons.items(), key=lambda item: item[1], reverse=True
    )
    blocked_text = ""
    if blocked:
        labels = "、".join(
            f"{_REASON_LABELS.get(reason, reason)} × {count}"
            for reason, count in blocked[:3]
        )
        blocked_text = f"{labels}，共 {signals.rejected_actions} 次"

    if signals.heal_verified:
        text = f"本地自愈执行后复检通过（{signals.heal_verified} 次）"
        return SELF_HEAL_SUCCESS, text
    if signals.heal_executed_rounds:
        text = f"本地自愈已执行 {signals.heal_executed_rounds} 轮，但复检均未通过"
        if blocked_text:
            text += f"；另有 {blocked_text} 被拒绝"
        return SELF_HEAL_FAILED, text
    if signals.executed_actions:
        text = f"动作已执行 {signals.executed_actions} 次，未获得复检结果"
        if blocked_text:
            text += f"；另有 {blocked_text} 被拒绝"
        return SELF_HEAL_FAILED, text
    if signals.rejected_actions:
        return SELF_HEAL_BLOCKED, f"自愈动作未实际执行（{blocked_text}）"
    return SELF_HEAL_NONE, "无本地自愈动作记录"


def assess_cause(
    *,
    category: str,
    boot_changed: bool,
    wan_ip_changed: bool,
    events: Iterable[Mapping[str, Any]],
    current_uptime: int | None = None,
) -> CauseAssessment:
    """根据故障窗口内的客户端事件推断可能原因。

    events 顺序应与客户端 seq 一致；只应包含故障期间补传的事件。
    """
    signals = _collect(events)
    boot_changed = boot_changed or signals.saw_device_reboot_event
    self_heal, self_heal_text = _self_heal_text(signals)
    recovery_id = ""
    if signals.heal_verified and signals.verified_recovery_id:
        recovery_id = signals.verified_recovery_id

    evidence: list[str] = []
    if boot_changed:
        suffix = ""
        if isinstance(current_uptime, int) and not isinstance(current_uptime, bool):
            if 0 <= current_uptime < 3600:
                suffix = f"，重启后运行 {current_uptime} 秒"
        evidence.append(f"设备重启（boot_id 变化{suffix}）")
    if signals.carrier_down:
        evidence.append("WAN 物理链路断开（carrier=0）")
    elif signals.carrier_unknown:
        evidence.append("WAN 物理链路状态无法读取（设备缺失或驱动异常）")
    if signals.carrier_recovered:
        evidence.append("恢复前 WAN 物理链路已重新连通（carrier=1）")
    if signals.pppoe_down:
        evidence.append("PPPoE 会话中断")
    elif signals.pppoe_unknown:
        evidence.append("PPPoE 状态无法读取")
    if signals.pppoe_recovered:
        evidence.append("PPPoE 拨号已重新建立")
    if signals.upstream_down:
        evidence.append("上游检测异常：" + "、".join(signals.upstream_down))
    if wan_ip_changed:
        evidence.append("WAN IP 发生变化（重新拨号）")

    if boot_changed and (signals.carrier_down or signals.carrier_unknown):
        code = CAUSE_DEVICE_REBOOT_LINK_DOWN
        reason = (
            "设备重启，且重启后 WAN 物理链路仍断开"
            "（常见于路由器与光猫同时断电）"
        )
    elif boot_changed:
        code = CAUSE_DEVICE_REBOOT
        reason = "设备重启（疑似断电或系统异常重启，未发现 WAN 物理链路中断）"
    elif signals.carrier_down:
        code = CAUSE_WAN_LINK_DOWN
        reason = "WAN 物理链路中断（光猫断电、网线松动或端口故障）"
    elif signals.carrier_unknown:
        code = CAUSE_WAN_LINK_UNKNOWN
        reason = "WAN 物理链路状态异常（设备缺失、驱动或读取失败）"
    elif signals.pppoe_down:
        code = CAUSE_PPPOE_DOWN
        reason = "PPPoE 拨号失败（账号、绑定或运营商侧异常）"
    elif signals.upstream_down:
        code = CAUSE_UPSTREAM_DOWN
        reason = "上游网络异常（运营商链路、DNS 或默认路由）"
    elif category == "agent_down":
        code = CAUSE_AGENT_DOWN
        reason = "agent 进程异常（主机与网络仍可用）"
    else:
        code = CAUSE_UNKNOWN
        reason = "主机或网络中断（设备侧未留下可用证据，可能断电或断网）"

    return CauseAssessment(
        code=code,
        reason=reason,
        evidence=tuple(evidence),
        self_heal=self_heal,
        self_heal_text=self_heal_text,
        recovery_id=recovery_id,
    )
