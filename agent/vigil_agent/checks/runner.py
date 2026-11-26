"""Check 执行器：单轮去重、依赖跳过与结果组装。"""

import time

from ..probes.base import DetectContext, Observation
from ..utils.text import truncate
from ..utils.validation import dependency_closure
from .expect import evaluate
from .model import CheckConfigError, dependency_graph, ordered_checks

STATUS_OK = "ok"
STATUS_CRITICAL = "critical"
STATUS_UNKNOWN = "unknown"
STATUS_SKIPPED = "skipped"

# V2 只使用 ok / critical / unknown / skipped；warn 预留给阈值告警
ABNORMAL_STATUSES = (STATUS_CRITICAL, STATUS_UNKNOWN)
BLOCKING_STATUSES = (STATUS_CRITICAL, STATUS_UNKNOWN, STATUS_SKIPPED)


def _result(check, status, latency_ms, evidence, checked_at):
    cid = str(check.get("id") or check.get("probe") or "")
    return {
        "check_id": cid,
        "id": cid,
        "name": check.get("name") or cid,
        "probe": check.get("probe") or "",
        "status": status,
        "latency_ms": int(latency_ms or 0),
        "evidence": evidence,
        "checked_at": checked_at,
    }


def _skipped(check, reason, checked_at, blocked_by=None):
    evidence = {"observed": {}, "expected": check.get("expect") or {}, "reason": reason}
    if blocked_by:
        evidence["blocked_by"] = blocked_by
    return _result(check, STATUS_SKIPPED, 0, evidence, checked_at)


class CheckRunner:
    """执行一组 Check，返回 {check_id: result}。"""

    def __init__(
        self,
        registry,
        config=None,
        runner=None,
        sys_class_net="/sys/class/net",
        http_get=None,
        clock=None,
        now=None,
    ):
        self.registry = registry
        self.config = config if isinstance(config, dict) else {}
        self.runner = runner
        self.sys_class_net = sys_class_net
        self.http_get = http_get
        self.clock = clock or time.monotonic
        self.now = now or time.time

    def run(self, checks):
        checks = list(checks or [])
        try:
            ordered = ordered_checks(checks)
        except CheckConfigError:
            ordered = checks
        results = {}
        cache = {}

        for check in ordered:
            checked_at = float(self.now())
            cid = str(check.get("id") or check.get("probe") or "")
            if not check.get("enabled", True):
                results[cid] = _skipped(check, "disabled", checked_at)
                continue

            blocked_by = ""
            for dep in check.get("depends_on") or []:
                status = results.get(str(dep), {}).get("status")
                if status in BLOCKING_STATUSES:
                    blocked_by = str(dep)
                    break
            if blocked_by:
                results[cid] = _skipped(
                    check, "dependency_failed", checked_at, blocked_by
                )
                continue

            probe = self.registry.get(check.get("probe"))
            if probe is None:
                results[cid] = _result(
                    check,
                    STATUS_UNKNOWN,
                    0,
                    {
                        "observed": {},
                        "expected": check.get("expect") or {},
                        "reason": "probe_missing",
                    },
                    checked_at,
                )
                continue

            started = self.clock()
            try:
                ctx = DetectContext(
                    self.config,
                    runner=self.runner,
                    sys_class_net=self.sys_class_net,
                    http_get=self.http_get,
                    clock=self.clock,
                    params=dict(check.get("params") or {}),
                    timeout=float(check.get("timeout") or 5),
                    cache=cache,
                )
                observation = probe.observe(ctx, dict(check.get("params") or {}))
                if not isinstance(observation, Observation):
                    observation = Observation(
                        {}, int((self.clock() - started) * 1000), "probe 返回值非法"
                    )
            except Exception as exc:  # probe 异常不应中断整轮检测
                observation = Observation(
                    {},
                    int((self.clock() - started) * 1000),
                    truncate("%s: %s" % (type(exc).__name__, exc)),
                )

            status, evidence = evaluate(check.get("expect"), observation)
            if observation.error:
                evidence.setdefault("error", observation.error)
            results[cid] = _result(
                check, status, observation.latency_ms, evidence, checked_at
            )
        return results


def aggregate_status(results, evaluate_cfg=None, check_refs=None, checks=None):
    """按 Guard 的 evaluate.mode 汇总 Check 状态。"""
    evaluate_cfg = evaluate_cfg if isinstance(evaluate_cfg, dict) else {}
    mode = evaluate_cfg.get("mode", "dependency_tree")
    refs = [str(item) for item in (check_refs or list(results))]

    if not results:
        return STATUS_SKIPPED

    if mode == "dependency_tree":
        graph = dependency_graph(checks) if checks else {}
        ids = [cid for cid in dependency_closure(refs, graph) if cid in results]
        statuses = [results[cid]["status"] for cid in ids]
        abnormal = [status for status in statuses if status in ABNORMAL_STATUSES]
        if abnormal:
            return STATUS_CRITICAL if STATUS_CRITICAL in abnormal else STATUS_UNKNOWN
        if STATUS_SKIPPED in statuses:
            return STATUS_SKIPPED
        return STATUS_OK

    statuses = [results[ref]["status"] for ref in refs if ref in results]
    if not statuses:
        return STATUS_SKIPPED

    if mode == "all":
        if all(status != STATUS_OK for status in statuses):
            if STATUS_CRITICAL in statuses:
                return STATUS_CRITICAL
            return STATUS_UNKNOWN if STATUS_UNKNOWN in statuses else STATUS_SKIPPED
        return STATUS_OK

    if mode == "any":
        abnormal = [status for status in statuses if status in ABNORMAL_STATUSES]
        if abnormal:
            return STATUS_CRITICAL if STATUS_CRITICAL in abnormal else STATUS_UNKNOWN
        return STATUS_SKIPPED if STATUS_SKIPPED in statuses else STATUS_OK

    if mode == "quorum":
        try:
            threshold = max(1, int(evaluate_cfg.get("quorum", 1)))
        except (TypeError, ValueError):
            threshold = 1
        failures = [status for status in statuses if status != STATUS_OK]
        if len(failures) >= threshold:
            if STATUS_CRITICAL in failures:
                return STATUS_CRITICAL
            return STATUS_UNKNOWN if STATUS_UNKNOWN in failures else STATUS_SKIPPED
        return STATUS_OK

    # 未知 mode 按最坏状态处理，避免静默放过异常
    for status in (STATUS_CRITICAL, STATUS_UNKNOWN, STATUS_SKIPPED):
        if status in statuses:
            return status
    return STATUS_OK
