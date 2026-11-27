"""本地 API 视图组装：状态、检测项、设置、事件与日志。

只负责把运行时数据整理为前端契约结构，不处理 HTTP 细节。
"""

import json
import os
import time
from urllib.parse import urlparse

from .. import PROTOCOL_VERSION
from ..checks.expect import evaluate
from ..checks.model import dependency_closure, dependency_graph
from ..checks.runner import (
    STATUS_CRITICAL,
    STATUS_OK,
    STATUS_UNKNOWN,
    CheckRunner,
)
from ..logstore import export_log_events, filter_events, read_log_events
from ..probes.base import (
    DetectContext,
    apply_param_defaults,
    first_ipv4,
    validate_params,
)
from ..runtime import (
    STATUS_WARN,
    derive_client_state,
    evidence_text,
    iso_utc,
    read_uptime,
)
from ..utils.proc import run_command

_LAN_IP_TTL = 30.0
_LAN_IP_CACHE = {"at": 0.0, "value": ""}


def _interface_status(interface):
    """读取逻辑接口状态；失败时返回 None。"""
    result = run_command(
        ["ubus", "call", "network.interface.%s" % interface, "status"], timeout=3
    )
    if result.timed_out or result.exit_code != 0 or not result.stdout.strip():
        return None
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _query_lan_ip():
    """优先读取 lan 逻辑接口地址，回退到本机地址表。"""
    ip = first_ipv4(_interface_status("lan"))
    if ip:
        return ip

    result = run_command(["ip", "-4", "-o", "addr", "show"], timeout=3)
    if result.timed_out or result.exit_code != 0:
        return ""

    fallback = ""
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4 or "inet" not in parts:
            continue
        position = parts.index("inet")
        if position + 1 >= len(parts):
            continue
        device = parts[1].rstrip(":")
        if device == "lo" or device.startswith(("ppp", "wwan")):
            continue
        address = parts[position + 1].split("/")[0]
        if device == "br-lan" or "lan" in device:
            return address
        if not fallback:
            fallback = address
    return fallback


def lan_ip(now=None):
    """本机局域网地址；结果缓存 30 秒，避免每次状态查询都调用 ubus。"""
    now = time.time() if now is None else now
    if now - float(_LAN_IP_CACHE.get("at") or 0) < _LAN_IP_TTL:
        return str(_LAN_IP_CACHE.get("value") or "")
    value = _query_lan_ip()
    _LAN_IP_CACHE["at"] = now
    _LAN_IP_CACHE["value"] = value
    return value


def _observed(app, checks, probe_id):
    """取第一个使用指定 probe 的检测项的观测数据。"""
    for check in app.checks:
        if check.get("probe") != probe_id:
            continue
        result = checks.get(check["id"]) or {}
        observed = (result.get("evidence") or {}).get("observed")
        if isinstance(observed, dict):
            return observed
    return {}


def _check_interval(app, check_id):
    """检测项被 Guard 引用时的最小间隔，用于前端展示。"""
    graph = dependency_graph(app.checks)
    intervals = []
    for guard in app.guards or []:
        if not guard.get("enabled", True):
            continue
        roots = guard.get("check_refs") or []
        if check_id not in dependency_closure(roots, graph):
            continue
        try:
            value = int((guard.get("policy") or {}).get("interval") or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            intervals.append(value)
    return min(intervals) if intervals else 30


def build_status(app):
    checks = app.state.snapshot_checks()
    wan = wan_info(app, checks)
    action_stats = app.executor.stats()
    now = time.time()
    ordered = [
        checks[check["id"]] for check in app.checks if check["id"] in checks
    ]

    last_heartbeat = app.state.last_heartbeat
    return {
        # 兼容字段（原有本地 API 消费者）
        "agent_version": app.state.agent_version,
        "registered": bool(app.transport.is_registered),
        "uptime": read_uptime(),
        "started_at": int(app.state.started_at),
        "storage_persistent": app.storage_persistent,
        "server": {
            "base_url": app.config["server"].get("base_url", ""),
            "last_heartbeat": last_heartbeat,
            "config_etag": app._etag or "",
        },
        "wan": wan,
        "checks": ordered,
        "spool": app.spool.stats(),
        "actions": action_stats,
        # 前端 ClientStatusSnapshot 契约字段
        "state": derive_client_state(checks),
        "boot_id": app.transport.boot_id,
        "wan_ip": wan.get("ip") or None,
        "lan_ip": lan_ip(now) or None,
        "carrier": "pppoe" if wan.get("pppoe_up") else (wan.get("device") or None),
        "pppoe_uptime": wan.get("pppoe_uptime"),
        "server_connected": bool(last_heartbeat),
        "last_heartbeat_at": iso_utc(last_heartbeat),
        "last_beacon_at": None,
        "guards": list(app.state.snapshot_guards().values()),
        "guards_source": app.guards_source,
        "guards_editable": app.guards_source == "local",
        "latest_checks": build_checks(app, checks),
        "maintenance": bool(float(action_stats.get("maintenance_until") or 0) > now),
        "generated_at": iso_utc(now),
    }


def build_checks(app, checks=None):
    """把检测项定义与最近结果整理成前端 CheckItem 列表。"""
    checks = checks if checks is not None else app.state.snapshot_checks()
    items = []
    for check in app.checks:
        cid = check["id"]
        result = checks.get(cid) or {}
        probe_id = check.get("probe") or ""
        items.append(
            {
                "id": cid,
                "name": check.get("name") or cid,
                "layer": probe_id,
                "probe": probe_id,
                "status": result.get("status") or STATUS_UNKNOWN,
                "latency_ms": result.get("latency_ms"),
                "interval": _check_interval(app, cid),
                "enabled": bool(check.get("enabled", True)),
                "evidence": evidence_text(result.get("evidence")),
                "updated_at": iso_utc(
                    result.get("checked_at") or app.state.last_check_at
                ),
                "params": dict(check.get("params") or {}),
                "expect": dict(check.get("expect") or {}),
                "timeout": check.get("timeout"),
                "depends_on": list(check.get("depends_on") or []),
                "probe_missing": app.registry.get(probe_id) is None,
                "has_result": bool(result),
            }
        )
    return items


def build_probes(app):
    """GET /api/probes：内置与本地探测器目录。"""
    items = []
    for entry in app.registry.catalog():
        item = dict(entry)
        item["valid"] = True
        item["error"] = ""
        items.append(item)

    known = {item["id"] for item in items}
    for package in app.probe_store.scan():
        if package.valid and package.id in known:
            continue
        items.append(
            {
                "id": package.id,
                "name": package.name or package.id,
                "description": package.description,
                "version": package.version,
                "source": "local",
                "remote_allowed": False,
                "params_schema": {},
                "default_expect": {},
                "default_timeout": package.timeout,
                "valid": False,
                "error": package.error or "未加载",
            }
        )
    return {"items": items, "directory": app.probe_store.directory}


def run_probe_test(app, probe_id, params=None, expect=None):
    """POST /api/probes/{id}/test：试运行探测器，不写入 Check 状态。"""
    probe = app.registry.get(probe_id)
    if probe is None:
        raise LookupError("probe 不存在：%s" % probe_id)

    values = dict(params) if isinstance(params, dict) else {}
    errors = validate_params(probe.params_schema, values)
    if errors:
        raise ValueError("；".join(errors))
    values = apply_param_defaults(probe.params_schema, values)

    expected = expect if isinstance(expect, dict) else dict(probe.default_expect)
    ctx = DetectContext(
        app.config,
        params=values,
        timeout=probe.default_timeout,
        check_id="test-%s" % probe_id,
    )
    observation = probe.observe(ctx, values)
    status, evidence = evaluate(expected, observation)
    if observation.error:
        evidence.setdefault("error", observation.error)
    return {
        "probe": probe.catalog_entry(),
        "params": values,
        "expect": expected,
        "status": status,
        "evidence": evidence,
        "observation": observation.to_dict(),
        "latency_ms": observation.latency_ms,
    }


def run_check_test(app, check_id):
    """POST /api/checks/{id}/test：按定义试运行单个检测项。"""
    target = None
    for check in app.checks:
        if check.get("id") == check_id:
            target = check
            break
    if target is None:
        raise LookupError("检测项不存在：%s" % check_id)

    results = CheckRunner(app.registry, app.config).run([target])
    result = results.get(check_id)
    if result is None:
        raise LookupError("检测项不存在：%s" % check_id)
    return {
        "check": dict(target),
        "result": result,
        "status": result.get("status") or STATUS_UNKNOWN,
        "latency_ms": result.get("latency_ms"),
        "evidence": result.get("evidence") or {},
    }


def build_settings(app):
    """GET /api/settings：客户端运行设置与版本信息。"""
    server = app.config.get("server") or {}
    stats = app.executor.stats()
    now = time.time()
    until = float(stats.get("maintenance_until") or 0)
    return {
        "version": app.state.agent_version,
        "protocol_version": PROTOCOL_VERSION,
        "config_path": app.config_path,
        "server": {
            "base_url": app.transport.base_url,
            "client_id": app.transport.client_id,
            "registered": app.transport.is_registered,
            "heartbeat_interval": int(server.get("heartbeat_interval") or 0),
            "config_poll_interval": int(server.get("config_poll_interval") or 0),
            "verify_tls": bool(server.get("verify_tls", True)),
        },
        "action_safety": {
            "global_enabled": app.executor.enabled,
            "max_risk_level": app.executor.max_level,
            "max_actions_per_hour": app.executor.max_per_hour,
            "allowed_services": list(app.executor.allowed_services),
        },
        "maintenance": {
            "active": until > now,
            "until": int(until),
            "remaining_seconds": max(0, int(until - now)),
        },
        "guards_source": app.guards_source,
        "generated_at": int(now),
    }


def parse_settings_update(payload):
    """校验 PUT /api/settings 请求体，返回规范化更新项。"""
    if not isinstance(payload, dict):
        raise ValueError("请求体必须是对象")

    base_url = None
    server_payload = payload.get("server")
    if isinstance(server_payload, dict) and "base_url" in server_payload:
        text = str(server_payload.get("base_url") or "").strip().rstrip("/")
        parsed = urlparse(text)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.netloc
            or any(char.isspace() for char in text)
        ):
            raise ValueError("服务器地址必须是 http(s) URL")
        base_url = text

    heartbeat = None
    if "heartbeat_interval" in payload:
        try:
            heartbeat = int(payload["heartbeat_interval"])
        except (TypeError, ValueError):
            raise ValueError("心跳间隔必须是整数") from None
        if not 10 <= heartbeat <= 300:
            raise ValueError("心跳间隔必须在 10-300 秒之间")

    safety_updates = {}
    safety_payload = payload.get("action_safety")
    if safety_payload is not None:
        if not isinstance(safety_payload, dict):
            raise ValueError("action_safety 必须是对象")
        if "global_enabled" in safety_payload:
            if not isinstance(safety_payload["global_enabled"], bool):
                raise ValueError("global_enabled 必须是布尔值")
            safety_updates["global_enabled"] = safety_payload["global_enabled"]
        if "max_risk_level" in safety_payload:
            level = str(safety_payload["max_risk_level"]).upper()
            if level != "L1":
                raise ValueError("当前仅支持 L1 动作")
            safety_updates["max_risk_level"] = level
        if "max_actions_per_hour" in safety_payload:
            try:
                limit = int(safety_payload["max_actions_per_hour"])
            except (TypeError, ValueError):
                raise ValueError("每小时动作上限必须是整数") from None
            if not 1 <= limit <= 60:
                raise ValueError("每小时动作上限必须在 1-60 之间")
            safety_updates["max_actions_per_hour"] = limit

    maintenance = None
    if "maintenance_minutes" in payload:
        try:
            maintenance = int(payload["maintenance_minutes"])
        except (TypeError, ValueError):
            raise ValueError("维护窗口必须是整数分钟") from None
        if not 0 <= maintenance <= 1440:
            raise ValueError("维护窗口必须在 0-1440 分钟之间")

    return {
        "base_url": base_url,
        "heartbeat": heartbeat,
        "safety_updates": safety_updates,
        "maintenance_minutes": maintenance,
    }


def list_events(state, spool, limit=50):
    items = state.events_snapshot(limit)
    try:
        pending = {entry.get("seq") for entry in spool.pending(limit=100000)}
    except Exception:  # spool 读取失败时不影响事件展示
        pending = set()
    return [serialize_event_item(item, pending) for item in items]


def serialize_event_item(item, pending):
    """把 spool 中的原始记录整理成前端 ClientEvent 契约结构。"""
    payload = item.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {"value": payload}
    level = payload.get("status") or payload.get("level")
    if level not in (STATUS_OK, STATUS_WARN, STATUS_CRITICAL, STATUS_UNKNOWN):
        result = str(payload.get("result") or "").lower()
        if payload.get("allowed") is False:
            level = STATUS_WARN
        elif result in ("success", "ok", "applied"):
            level = STATUS_OK
        elif result in ("failed", "error", "timeout"):
            level = STATUS_CRITICAL
        else:
            level = STATUS_UNKNOWN
    seq = item.get("seq")
    return {
        "event_id": item.get("event_id") or "",
        "ts": iso_utc(item.get("ts")),
        "type": item.get("type"),
        "module": item.get("domain") or None,
        "level": level,
        "message": payload.get("message") or payload.get("reason") or None,
        "uploaded": seq not in pending,
        "payload": payload,
    }


def query_logs(
    config,
    state,
    limit=200,
    level=None,
    event=None,
    guard_id=None,
    recovery_id=None,
    since=None,
):
    limit = max(1, min(1000, int(limit)))
    storage = config.get("storage") or {}
    path = str(storage.get("log_path") or "")
    backups = int(storage.get("log_backups") or 2)
    if path and os.path.exists(path):
        events = read_log_events(
            path,
            limit=limit,
            level=level,
            event=event,
            guard_id=guard_id,
            recovery_id=recovery_id,
            since=since,
            backup_count=backups,
        )
        if events:
            return events
    return filter_events(
        state.logs_snapshot(500),
        level=level,
        event=event,
        guard_id=guard_id,
        recovery_id=recovery_id,
        since=since,
        limit=limit,
    )


def export_logs(
    config,
    state,
    level=None,
    event=None,
    guard_id=None,
    recovery_id=None,
    since=None,
):
    storage = config.get("storage") or {}
    path = str(storage.get("log_path") or "")
    backups = int(storage.get("log_backups") or 2)
    data = b""
    if path and os.path.exists(path):
        data = export_log_events(
            path,
            level=level,
            event=event,
            guard_id=guard_id,
            recovery_id=recovery_id,
            since=since,
            backup_count=backups,
        )
    if data:
        return data
    items = filter_events(
        state.logs_snapshot(500),
        level=level,
        event=event,
        guard_id=guard_id,
        recovery_id=recovery_id,
        since=since,
    )
    lines = [json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in items]
    return ("\n".join(lines) + "\n").encode("utf-8") if lines else b""


def checks_snapshot(app, checks):
    """心跳中的检测项状态快照，按 check_id 索引。"""
    snapshot = {}
    for check in app.checks:
        cid = str(check["id"])
        result = checks.get(cid) or {}
        item = {
            "check_id": cid,
            "name": check.get("name") or cid,
            "probe": check.get("probe") or "",
            "status": result.get("status") or STATUS_UNKNOWN,
            "latency_ms": int(result.get("latency_ms") or 0),
            "evidence": result.get("evidence") if isinstance(result.get("evidence"), dict) else {},
        }
        checked_at = result.get("checked_at")
        if checked_at:
            item["checked_at"] = iso_utc(checked_at)
        snapshot[cid] = item
    return snapshot


def wan_ip(app, checks):
    return str(_observed(app, checks, "route").get("egress_ip") or "")


def wan_info(app, checks):
    carrier = _observed(app, checks, "carrier")
    pppoe = _observed(app, checks, "pppoe")
    return {
        "device": carrier.get("device") or "",
        "interface": str(
            pppoe.get("interface")
            or (app.config.get("action") or {}).get("wan_interface")
            or "wan"
        ),
        "ip": wan_ip(app, checks),
        "carrier": carrier.get("carrier", ""),
        "pppoe_up": bool(pppoe.get("up")),
        "pppoe_uptime": int(pppoe.get("uptime") or 0),
    }
