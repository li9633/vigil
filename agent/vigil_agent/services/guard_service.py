"""Guard 领域服务：调度节拍、状态评估、配置校验与本地编辑。"""

import copy
import logging
import time

from ..checks.model import (
    CheckConfigError,
    check_map,
    dependency_closure,
    dependency_graph,
    normalize_checks,
    validate_checks,
)
from ..checks.runner import STATUS_SKIPPED, CheckRunner, aggregate_status
from ..config import save_config
from ..guards.model import normalize_guard, validate_guards
from ..logstore import log_event
from ..utils.proc import CommandRunner

LOGGER = logging.getLogger("vigil.agent")


def detect_loop_interval(guards):
    """计算 detect 调度节拍：所有启用 Guard 间隔的最小值。"""
    intervals = []
    for guard in guards or []:
        if not guard.get("enabled", True):
            continue
        try:
            value = int((guard.get("policy") or {}).get("interval") or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            intervals.append(value)
    return min(intervals) if intervals else 30


def run_guard_checks(guard, checks, registry, config, runner=None):
    """执行 Guard 引用集合的传递依赖闭包，返回 {check_id: result}。"""
    by_id = check_map(checks)
    graph = dependency_graph(checks)
    roots = [str(item) for item in guard.get("check_refs") or []]
    ids = dependency_closure(roots, graph) if roots else list(by_id)
    selected = [by_id[item] for item in ids if item in by_id]
    if not selected:
        return {}
    return CheckRunner(registry, config, runner=runner).run(selected)


def evaluate_guards(
    guards,
    checks,
    config,
    registry,
    fsm,
    last_run,
    now=None,
    previous=None,
):
    """执行到期的 Guard Check，并推进各自的状态机。

    每个 Guard 按 policy.interval 节流；未到期时沿用上一轮快照，
    既保证心跳中的状态连续，也避免重复探测。返回 (snapshots, fresh_results)。
    """
    now = float(now if now is not None else time.time())
    previous = previous or {}
    command_runner = CommandRunner()
    snapshots = {}
    fresh = {}
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

        results = run_guard_checks(
            guard, checks, registry, config, runner=command_runner
        )
        fresh.update(results)
        check_status = aggregate_status(
            results, guard.get("evaluate"), guard.get("check_refs"), checks
        )
        fsm_state = fsm.process(guard, check_status, now=now)
        if LOGGER.isEnabledFor(logging.DEBUG):
            for result in results.values():
                log_event(
                    "debug",
                    "guard.check",
                    "Check 结果",
                    guard={
                        "id": guard_id,
                        "name": name,
                        "template": guard.get("template", ""),
                    },
                    check={"id": result.get("check_id"), "probe": result.get("probe")},
                    status=result.get("status"),
                    latency_ms=result.get("latency_ms"),
                    evidence=result.get("evidence"),
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
            "checks": results,
        }
        last_run[guard_id] = now
    return snapshots, fresh


def build_guards(app):
    """GET /api/guards：返回当前生效的 Guard 配置及来源。"""
    return {
        "items": copy.deepcopy(app.guards),
        "source": app.guards_source,
        "editable": app.guards_source == "local",
    }


def validate_local_guards(guards, checks):
    """校验本地提交的 Guard 列表，返回规范化副本。"""
    if not isinstance(guards, list):
        raise ValueError("guards 必须是数组")
    candidate = [normalize_guard(item) for item in guards]
    errors = []
    validate_guards({"guards": candidate}, errors, checks=checks)
    if errors:
        raise ValueError("；".join(errors))
    return candidate


def update_local_guards(app, guards):
    """PUT /api/guards：本地整表替换 Guard 配置并立即生效。"""
    if app.guards_source == "server":
        raise ValueError("managed_by_server")

    candidate = validate_local_guards(guards, app.checks)
    with app._detect_lock:
        app.config["guards"] = copy.deepcopy(candidate)
        app.guards = copy.deepcopy(candidate)
        app.guards_source = "local"
        app._server_guards = None
        app._guard_last_run.clear()
        app.detect_interval = detect_loop_interval(app.guards)
        app.scheduler.update_interval("detect", app.detect_interval)

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


def plan_server_config(
    server_config,
    local_checks,
    local_guards,
    current_checks,
    current_guards,
    current_source,
    current_server_guards,
    registry,
):
    """计算服务端下发 checks + guards 的整体应用方案。

    返回 (decision, errors)：
    - decision 为 None 表示保持现状
    - errors 非空表示校验失败，调用方只记日志并保持当前配置
    """
    if not isinstance(server_config, dict):
        return None, []

    has_checks = "checks" in server_config
    has_guards = "guards" in server_config
    if not has_checks and not has_guards:
        if current_source == "local" and current_server_guards is None:
            # 已经是本地配置，无需重复替换与重置节流
            return None, []
        return {
            "checks": copy.deepcopy(local_checks),
            "guards": copy.deepcopy(local_guards),
            "source": "local",
            "server_guards": None,
        }, []

    if server_config.get("schema_version") != 3:
        return None, ["schema_version 必须为 3"]

    errors = []
    if has_checks:
        raw_checks = server_config.get("checks")
        if not isinstance(raw_checks, list):
            return None, ["checks 必须是数组"]
        try:
            candidate_checks = normalize_checks(raw_checks, registry)
        except CheckConfigError as exc:
            return None, [str(exc)]
        validate_checks(candidate_checks, registry, errors, remote=True)
        if errors:
            return None, errors
    else:
        candidate_checks = copy.deepcopy(current_checks)

    if has_guards:
        raw_guards = server_config.get("guards")
        if not isinstance(raw_guards, list):
            return None, ["guards 必须是数组"]
        candidate_guards = [
            normalize_guard(item) if isinstance(item, dict) else item
            for item in raw_guards
        ]
    else:
        candidate_guards = copy.deepcopy(current_guards)

    # 远端配置允许空 guards（只管理 checks），但仍校验 refs 与策略字段
    validate_guards(
        {"guards": candidate_guards}, errors, checks=candidate_checks, allow_empty=True
    )
    if errors:
        return None, errors

    return {
        "checks": candidate_checks,
        "guards": candidate_guards,
        "source": "server",
        "server_guards": copy.deepcopy(candidate_guards),
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
