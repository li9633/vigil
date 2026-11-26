"""Guard 领域服务：调度节拍、状态评估、配置校验与本地编辑。"""

import copy
import logging
import time

from ..config import save_config
from ..detect.base import STATUS_SKIPPED, CommandRunner
from ..guards import aggregate_status, run_checks, validate_guards
from ..logstore import log_event

LOGGER = logging.getLogger("vigil.agent")


def detect_loop_interval(config, guards):
    """计算 detect 调度节拍：旧 detect.interval 与启用 Guard 间隔的最小值。"""
    try:
        intervals = [int(config["detect"]["interval"])]
    except (KeyError, TypeError, ValueError):
        intervals = [30]
    for guard in guards or []:
        if not guard.get("enabled", True):
            continue
        try:
            value = int((guard.get("policy") or {}).get("interval") or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            intervals.append(value)
    return min(intervals)


def evaluate_guards(guards, config, fsm, last_run, now=None, previous=None):
    """执行到期的 Guard Check，并推进各自的状态机。

    每个 Guard 按 policy.interval 节流；未到期时沿用上一轮快照，
    既保证心跳中的状态连续，也避免重复探测。
    """
    now = float(now if now is not None else time.time())
    previous = previous or {}
    runner = CommandRunner()
    snapshots = {}
    for guard in guards:
        guard_id = str(guard.get("id"))
        name = guard.get("name") or guard_id
        if not guard.get("enabled", True):
            last_run.pop(guard_id, None)
            snapshots[guard_id] = {
                "id": guard_id,
                "name": name,
                "template": guard.get("template", ""),
                "status": "DISABLED",
                "check_status": STATUS_SKIPPED,
                "checks": {},
            }
            continue

        interval = int((guard.get("policy") or {}).get("interval") or 30)
        last = last_run.get(guard_id)
        cached = previous.get(guard_id)
        if last is not None and cached is not None and now - last < interval:
            snapshots[guard_id] = cached
            continue

        checks = run_checks(guard, config, runner=runner)
        check_status = aggregate_status(checks, guard.get("evaluate"))
        fsm_state = fsm.process(guard, check_status, now=now)
        if LOGGER.isEnabledFor(logging.DEBUG):
            for check in checks.values():
                log_event(
                    "debug",
                    "guard.check",
                    "Check 结果",
                    guard={"id": guard_id, "name": name, "template": guard.get("template", "")},
                    check={"id": check.get("id"), "probe": check.get("probe")},
                    status=check.get("status"),
                    latency_ms=check.get("latency_ms"),
                    evidence=check.get("evidence"),
                )
        snapshots[guard_id] = {
            "id": guard_id,
            "name": name,
            "template": guard.get("template", ""),
            "status": fsm_state["status"],
            "check_status": check_status,
            "failure_count": fsm_state["failure_count"],
            "success_count": fsm_state["success_count"],
            "recovery_rounds": fsm_state["recovery_rounds"],
            "rearm_at": fsm_state["rearm_at"],
            "last_recovery_id": str(fsm.snapshot(guard_id).get("last_recovery_id", "")),
            "checks": checks,
        }
        last_run[guard_id] = now
    return snapshots


def build_guards(app):
    """GET /api/guards：返回当前生效的 Guard 配置及来源。"""
    return {
        "items": copy.deepcopy(app.guards),
        "source": app.guards_source,
        "editable": app.guards_source == "local",
    }


def validate_local_guards(guards):
    """校验本地提交的 Guard 列表，返回深拷贝副本。"""
    candidate = copy.deepcopy(guards)
    errors = []
    validate_guards({"guards": candidate}, errors)
    if errors:
        raise ValueError("；".join(errors))
    return candidate


def update_local_guards(app, guards):
    """PUT /api/guards：本地整表替换 Guard 配置并立即生效。"""
    if app.guards_source == "server":
        raise ValueError("managed_by_server")
    if not isinstance(guards, list):
        raise ValueError("guards 必须是数组")

    candidate = validate_local_guards(guards)
    with app._detect_lock:
        app.config["guards"] = copy.deepcopy(candidate)
        app.guards = copy.deepcopy(candidate)
        app.guards_source = "local"
        app._server_guards = None
        app._guard_last_run.clear()
        app.scheduler.update_interval(
            "detect", detect_loop_interval(app.config, app.guards)
        )

    save_config(app.config, app.config_path)
    app._record_event(
        "config_updated",
        "guards",
        {
            "source": "local",
            "count": len(candidate),
            "guards": [guard.get("id") for guard in candidate],
        },
    )
    log_event(
        "info",
        "guard_config.update",
        "本地守护项配置已更新并持久化",
        count=len(candidate),
        source="local",
    )
    return candidate


def plan_server_guards(server_config, local_guards, current_source, current_server_guards):
    """计算服务端下发 guards 的应用方案。

    返回 (decision, errors)：
    - decision 为 None 表示保持现状
    - errors 非空表示下发的 guards 校验失败，调用方只记日志
    """
    raw = server_config.get("guards") if isinstance(server_config, dict) else None
    if isinstance(raw, list):
        errors = []
        validate_guards({"guards": raw}, errors)
        if errors:
            return None, errors
        return {
            "guards": copy.deepcopy(raw),
            "source": "server",
            "server_guards": copy.deepcopy(raw),
        }, []

    if current_source == "local" and current_server_guards is None:
        # 已经是本地配置，无需重复替换与重置节流
        return None, []
    return {
        "guards": copy.deepcopy(local_guards or []),
        "source": "local",
        "server_guards": None,
    }, []


def heartbeat_payload(snapshots):
    """心跳中的 Guard 快照，按 guard_id 索引。"""
    payload = {}
    for guard_id, snapshot in snapshots.items():
        payload[guard_id] = {
            "id": guard_id,
            "name": snapshot.get("name", ""),
            "template": snapshot.get("template", ""),
            "status": snapshot.get("status", ""),
            "check_status": snapshot.get("check_status", ""),
            "failure_count": snapshot.get("failure_count", 0),
            "checks": snapshot.get("checks", {}),
            "last_recovery_id": snapshot.get("last_recovery_id", ""),
        }
    return payload
