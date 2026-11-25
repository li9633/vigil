"""Probe / Check 执行器。

把一个 Guard 的 checks 按依赖顺序执行，输出每个 Check 的原始状态。
本模块只做检测，不做失败计数与动作决策；状态机在 guards.runner 中实现。
"""

from ..detect import carrier, dns, http, pppoe, route
from ..detect.base import (
    STATUS_CRITICAL,
    STATUS_OK,
    STATUS_SKIPPED,
    STATUS_UNKNOWN,
    STATUS_WARN,
    DetectContext,
    make_result,
    truncate,
)

BUILTIN_PROBES = {
    "carrier": carrier.detect,
    "pppoe": pppoe.detect,
    "route": route.detect,
    "dns": dns.detect,
    "http": http.detect,
}

ABNORMAL = (STATUS_WARN, STATUS_CRITICAL, STATUS_UNKNOWN)


def check_id(check):
    """Check 的稳定标识：显式 id 优先，否则用 probe 名。"""
    return str(check.get("id") or check.get("probe"))


def _ordered(checks):
    """按 depends_on 做拓扑排序；未知依赖忽略，循环依赖抛 ValueError。"""
    by_id = {check_id(check): check for check in checks}
    order = []
    state = {}

    def visit(name):
        if name in state:
            if state[name] == 1:
                raise ValueError("Check 存在循环依赖：%s" % name)
            return
        state[name] = 1
        check = by_id[name]
        for dep in check.get("depends_on") or []:
            if dep in by_id:
                visit(dep)
        state[name] = 2
        order.append(name)

    for name in by_id:
        visit(name)
    return [by_id[name] for name in order]


def _params(check):
    params = dict(check.get("params") or {})
    if check.get("timeout") is not None:
        params["timeout"] = check["timeout"]
    return params


def run_checks(
    guard,
    config,
    runner=None,
    sys_class_net="/sys/class/net",
    http_get=None,
    clock=None,
):
    """执行一个 Guard 的全部 Check，返回 {check_id: result}。"""
    checks = guard.get("checks") or []
    results = {}
    shared_cache = {}
    runner_obj = runner

    for check in _ordered(checks):
        cid = check_id(check)
        probe = str(check.get("probe") or "")

        blocked_by = ""
        for dep in check.get("depends_on") or []:
            dep_status = results.get(dep, {}).get("status")
            if dep_status in (STATUS_CRITICAL, STATUS_SKIPPED):
                blocked_by = dep
                break

        if blocked_by:
            results[cid] = {
                "id": cid,
                "probe": probe,
                "status": STATUS_SKIPPED,
                "latency_ms": 0,
                "evidence": {
                    "reason": "上游 %s 异常，跳过本项检测" % blocked_by,
                    "blocked_by": blocked_by,
                },
            }
            continue

        func = BUILTIN_PROBES.get(probe)
        if func is None:
            results[cid] = {
                "id": cid,
                "probe": probe,
                "status": STATUS_UNKNOWN,
                "latency_ms": 0,
                "evidence": {"reason": "未实现的探测器：%s" % probe},
            }
            continue

        ctx = DetectContext(
            config,
            runner=runner_obj,
            sys_class_net=sys_class_net,
            http_get=http_get,
            clock=clock,
            params=_params(check),
            cache=shared_cache,
        )
        started = ctx.clock()
        try:
            raw = func(ctx)
        except Exception as exc:
            raw = make_result(
                probe,
                STATUS_UNKNOWN,
                int((ctx.clock() - started) * 1000),
                {"error": truncate("%s: %s" % (type(exc).__name__, exc), 512)},
            )

        results[cid] = {
            "id": cid,
            "probe": probe,
            "status": str(raw.get("status", STATUS_UNKNOWN)),
            "latency_ms": int(raw.get("latency_ms") or 0),
            "evidence": raw.get("evidence")
            if isinstance(raw.get("evidence"), dict)
            else {},
        }
    return results


def aggregate_status(results, evaluate=None):
    """按 Guard 的组合方式汇总 Check 状态。"""
    evaluate = evaluate or {}
    mode = evaluate.get("mode", "dependency_tree")
    active = [
        item for item in results.values() if item.get("status") != STATUS_SKIPPED
    ]
    if not active:
        return STATUS_SKIPPED

    statuses = [item["status"] for item in active]
    critical_count = statuses.count(STATUS_CRITICAL)

    if mode == "all":
        if all(status == STATUS_CRITICAL for status in statuses):
            return STATUS_CRITICAL
        if all(status in ABNORMAL for status in statuses):
            return STATUS_WARN
        return STATUS_OK

    if mode == "quorum":
        try:
            threshold = max(1, int(evaluate.get("quorum", 1)))
        except (TypeError, ValueError):
            threshold = 1
        return STATUS_CRITICAL if critical_count >= threshold else (
            STATUS_WARN if STATUS_CRITICAL in statuses or STATUS_WARN in statuses else STATUS_OK
        )

    # dependency_tree 与 any 都取最坏状态
    for status in (STATUS_CRITICAL, STATUS_WARN, STATUS_UNKNOWN):
        if status in statuses:
            return status
    return STATUS_OK if STATUS_OK in statuses else STATUS_UNKNOWN
