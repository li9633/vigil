"""Check 与 Guard 配置校验（schema v3）。

服务端保存前做完整校验：id 格式与唯一性、probe 是否在目录且允许远程引用、
params/expect 结构、timeout、依赖闭包与环、Guard 的 check_refs 引用。
"""

from __future__ import annotations

import copy
import re
import uuid
from typing import Any

from app.domain.probe_catalog import (
    MAX_TIMEOUT,
    apply_param_defaults,
    builtin_probe,
    validate_expect,
    validate_params,
)
from app.errors import ValidationError

CHECK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

DEFAULT_POLICY = {
    "interval": 30,
    "fail_threshold": 2,
    "fail_mode": "consecutive",
    "fail_window": 90,
    "success_threshold": 1,
    "cooldown": 300,
    "max_actions_per_hour": 3,
    "gave_up_after": 3,
    "backoff": "exponential",
}

ALLOWED_ACTIONS = {
    "ifup_wan": {"requires_target": False, "risk": "L1"},
    "restart_service": {"requires_target": True, "risk": "L1"},
}

EVALUATE_MODES = ("dependency_tree", "all", "any", "quorum")


def is_check_id(value: Any) -> bool:
    return isinstance(value, str) and bool(CHECK_ID_RE.match(value))


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _topological_sort(graph: dict[str, list[str]]) -> None:
    """依赖无环校验；发现环时抛 ValidationError。"""
    state: dict[str, str] = {}

    def visit(node: str, stack: list[str]) -> None:
        status = state.get(node)
        if status == "done":
            return
        if status == "visiting":
            cycle = " -> ".join(stack + [node])
            raise ValidationError(f"检测项依赖存在环：{cycle}")
        state[node] = "visiting"
        for dep in graph.get(node, ()):
            visit(dep, stack + [node])
        state[node] = "done"

    for node in graph:
        visit(node, [])


def normalize_check(raw: Any, index: int) -> dict[str, Any]:
    """校验并规范化单个 Check；不合法时抛 ValidationError。"""
    where = f"checks[{index}]"
    if not isinstance(raw, dict):
        raise ValidationError(f"{where} 必须是对象")

    cid = raw.get("id")
    if not is_check_id(cid):
        raise ValidationError(f"{where}.id 非法：{cid!r}")

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValidationError(f"{where}.name 必须是非空字符串")

    probe_id = raw.get("probe")
    probe = builtin_probe(probe_id) if isinstance(probe_id, str) else None
    if probe is None:
        raise ValidationError(f"{where}.probe 不存在：{probe_id!r}")
    if not probe["remote_allowed"]:
        raise ValidationError(f"{where}.probe 不允许服务端引用：{probe_id}")

    params = raw.get("params", {})
    if not isinstance(params, dict):
        raise ValidationError(f"{where}.params 必须是对象")
    param_errors = validate_params(probe["params_schema"], params)
    if param_errors:
        raise ValidationError(f"{where}.params：" + "；".join(param_errors))
    params = apply_param_defaults(probe["params_schema"], params)

    expect = raw.get("expect")
    if expect is None:
        expect = copy.deepcopy(probe["default_expect"])
    expect_errors = validate_expect(expect)
    if expect_errors:
        raise ValidationError(f"{where}.expect：" + "；".join(expect_errors))

    timeout = raw.get("timeout", probe["default_timeout"])
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not 0 < float(timeout) <= MAX_TIMEOUT
    ):
        raise ValidationError(f"{where}.timeout 必须是 (0, 60] 的数值")

    deps = raw.get("depends_on", [])
    if not isinstance(deps, list) or not all(isinstance(item, str) for item in deps):
        raise ValidationError(f"{where}.depends_on 必须是字符串数组")

    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValidationError(f"{where}.enabled 必须是布尔值")

    return {
        "id": str(cid),
        "name": name.strip(),
        "probe": str(probe_id),
        "params": params,
        "expect": copy.deepcopy(expect),
        "timeout": float(timeout),
        "depends_on": [str(dep) for dep in deps],
        "enabled": enabled,
    }


def validate_and_normalize_checks(raw_checks: Any) -> list[dict[str, Any]]:
    """校验并规范化整表 Check，返回按原顺序的规范化列表。"""
    if not isinstance(raw_checks, list):
        raise ValidationError("checks 必须是数组")

    items = [normalize_check(raw, index) for index, raw in enumerate(raw_checks)]

    seen: set[str] = set()
    for item in items:
        cid = item["id"]
        if cid in seen:
            raise ValidationError(f"检测项 id 重复：{cid}")
        seen.add(cid)

    for index, item in enumerate(items):
        for dep in item["depends_on"]:
            if dep == item["id"]:
                raise ValidationError(f"checks[{index}] 不能依赖自身")
            if dep not in seen:
                raise ValidationError(f"checks[{index}].depends_on 引用了不存在的检测项：{dep}")

    _topological_sort(
        {item["id"]: list(item["depends_on"]) for item in items}
    )
    return items


def check_ids(checks: list[dict[str, Any]]) -> set[str]:
    return {str(item["id"]) for item in checks if isinstance(item, dict) and item.get("id")}


def _normalize_policy(raw: Any) -> dict[str, Any]:
    policy = copy.deepcopy(DEFAULT_POLICY)
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key in policy and value is not None:
                policy[key] = value
    return policy


def normalize_guard(raw: Any, index: int) -> dict[str, Any]:
    """校验并规范化单个 Guard（不校验 check_refs 是否存在）。"""
    where = f"guards[{index}]"
    if not isinstance(raw, dict):
        raise ValidationError(f"{where} 必须是对象")

    raw_id = raw.get("id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise ValidationError(f"{where}.id 必须是 UUID")
    try:
        guard_id = str(uuid.UUID(raw_id.strip()))
    except ValueError as exc:
        raise ValidationError(f"{where}.id 必须是 UUID") from exc

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValidationError(f"{where}.name 必须是非空字符串")

    template = raw.get("template", "")
    if not isinstance(template, str):
        raise ValidationError(f"{where}.template 必须是字符串")

    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValidationError(f"{where}.enabled 必须是布尔值")

    refs = raw.get("check_refs")
    if not isinstance(refs, list) or not refs:
        raise ValidationError(f"{where}.check_refs 必须是非空数组")
    if not all(isinstance(item, str) and item.strip() for item in refs):
        raise ValidationError(f"{where}.check_refs 必须是字符串数组")
    if len(set(refs)) != len(refs):
        raise ValidationError(f"{where}.check_refs 存在重复项")

    policy = raw.get("policy")
    if not isinstance(policy, dict):
        raise ValidationError(f"{where}.policy 必须是对象")
    normalized_policy = _normalize_policy(policy)
    threshold = normalized_policy["fail_threshold"]
    if not _is_int(threshold) or not 1 <= threshold <= 10:
        raise ValidationError(f"{where}.policy.fail_threshold 必须是 1-10 的整数")
    interval = normalized_policy["interval"]
    if not _is_int(interval) or not 5 <= interval <= 3600:
        raise ValidationError(f"{where}.policy.interval 必须是 5-3600 的整数")
    if normalized_policy["fail_mode"] not in ("consecutive", "window"):
        raise ValidationError(f"{where}.policy.fail_mode 只能是 consecutive 或 window")
    window = normalized_policy["fail_window"]
    if not _is_int(window) or not 5 <= window <= 86400:
        raise ValidationError(f"{where}.policy.fail_window 必须是 5-86400 的整数")
    success = normalized_policy["success_threshold"]
    if not _is_int(success) or not 1 <= success <= 10:
        raise ValidationError(f"{where}.policy.success_threshold 必须是 1-10 的整数")
    cooldown = normalized_policy["cooldown"]
    if not _is_int(cooldown) or not 0 <= cooldown <= 86400:
        raise ValidationError(f"{where}.policy.cooldown 必须是 0-86400 的整数")
    max_per_hour = normalized_policy["max_actions_per_hour"]
    if not _is_int(max_per_hour) or not 1 <= max_per_hour <= 100:
        raise ValidationError(f"{where}.policy.max_actions_per_hour 必须是 1-100 的整数")
    gave_up = normalized_policy["gave_up_after"]
    if not _is_int(gave_up) or not 1 <= gave_up <= 20:
        raise ValidationError(f"{where}.policy.gave_up_after 必须是 1-20 的整数")

    recovery = raw.get("recovery")
    if not isinstance(recovery, list) or not recovery:
        raise ValidationError(f"{where}.recovery 必须是非空数组")
    for r_index, step in enumerate(recovery):
        r_where = f"{where}.recovery[{r_index}]"
        if not isinstance(step, dict):
            raise ValidationError(f"{r_where} 必须是对象")
        action = step.get("action")
        if action not in ALLOWED_ACTIONS:
            raise ValidationError(f"{r_where}.action 不在动作库内：{action!r}")
        target = step.get("target")
        if ALLOWED_ACTIONS[action]["requires_target"]:
            if not isinstance(target, str) or not target.strip():
                raise ValidationError(f"{r_where}.target 必须是非空字符串")
        elif target not in (None, ""):
            raise ValidationError(f"{r_where}.target 必须为空")
        verify_after = step.get("verify_after", 20)
        if not _is_int(verify_after) or not 0 <= verify_after <= 300:
            raise ValidationError(f"{r_where}.verify_after 必须是 0-300 的整数")
        if not isinstance(step.get("continue_on_failure", False), bool):
            raise ValidationError(f"{r_where}.continue_on_failure 必须是布尔值")

    limits = raw.get("limits", {})
    if not isinstance(limits, dict):
        raise ValidationError(f"{where}.limits 必须是对象")
    if limits.get("max_risk_level", "L1") not in ("L0", "L1"):
        raise ValidationError(f"{where}.limits.max_risk_level 只能是 L0 或 L1")

    evaluate = raw.get("evaluate", {})
    if not isinstance(evaluate, dict):
        raise ValidationError(f"{where}.evaluate 必须是对象")
    mode = evaluate.get("mode", "dependency_tree")
    if mode not in EVALUATE_MODES:
        raise ValidationError(f"{where}.evaluate.mode 只能是 dependency_tree/all/any/quorum")
    quorum = evaluate.get("quorum", 1)
    if not _is_int(quorum) or not 1 <= quorum <= 100:
        raise ValidationError(f"{where}.evaluate.quorum 必须是 1-100 的整数")

    return {
        "id": guard_id,
        "name": name.strip(),
        "template": template,
        "enabled": enabled,
        "check_refs": [str(ref) for ref in refs],
        "evaluate": {"mode": mode, "quorum": quorum},
        "policy": normalized_policy,
        "recovery": copy.deepcopy(recovery),
        "limits": copy.deepcopy(limits),
    }


def validate_and_normalize_guards(
    raw_guards: Any, available_checks: set[str]
) -> list[dict[str, Any]]:
    """校验整表 Guard，并确认 check_refs 均指向已存在的检测项。"""
    if not isinstance(raw_guards, list):
        raise ValidationError("guards 必须是数组")

    items = [normalize_guard(raw, index) for index, raw in enumerate(raw_guards)]

    seen_ids: set[str] = set()
    seen_templates: set[str] = set()
    for index, item in enumerate(items):
        guard_id = item["id"]
        if guard_id in seen_ids:
            raise ValidationError(f"guard_id 重复：{guard_id}")
        seen_ids.add(guard_id)

        template = item.get("template") or ""
        if template:
            if template in seen_templates:
                raise ValidationError(f"guards[{index}].template 重复：{template}")
            seen_templates.add(template)

        for ref in item["check_refs"]:
            if ref not in available_checks:
                raise ValidationError(
                    f"guards[{index}].check_refs 引用了不存在的检测项：{ref}"
                )
    return items
