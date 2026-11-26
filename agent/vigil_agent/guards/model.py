"""Guard（守护项）配置模型。

Guard 是完整监控单元：`check_refs` + 触发策略 + 恢复动作。
配置层只负责数据结构、缺省值与校验，检测与状态机由 checks / guards.fsm 实现。
"""

import copy
import uuid

# 内置网络守护项的固定 UUID，保证同一客户端升级后引用不变
BUILTIN_NETWORK_ID = "6f2c1a4e-9b7d-4c3a-8e51-2d4b6a8f0c37"
BUILTIN_NETWORK_TEMPLATE = "network.up"

# 触发策略缺省值，对应设计文档 3.7
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

DEFAULT_ACTION_SAFETY = {
    "global_enabled": True,
    "max_risk_level": "L1",
    "max_actions_per_hour": 3,
    "maintenance_windows": [],
}

# 动作库白名单，当前只允许 L1
ALLOWED_ACTIONS = {
    "ifup_wan": {"requires_target": False, "risk": "L1"},
    "restart_service": {"requires_target": True, "risk": "L1"},
}


class GuardConfigError(Exception):
    """Guard 配置非法。"""


def _normalize_policy(raw):
    policy = copy.deepcopy(DEFAULT_POLICY)
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key in policy and value is not None:
                policy[key] = value
    return policy


def normalize_guard(raw):
    """补齐缺省字段，返回深拷贝；不修改入参。"""
    if not isinstance(raw, dict):
        raise GuardConfigError("Guard 必须是对象")
    guard = copy.deepcopy(raw)

    guard["id"] = str(guard.get("id") or uuid.uuid4())
    guard["name"] = str(guard.get("name") or "未命名守护项")
    guard["template"] = str(guard.get("template") or "")
    guard["enabled"] = bool(guard.get("enabled", True))
    guard["policy"] = _normalize_policy(guard.get("policy"))

    refs = guard.get("check_refs")
    guard["check_refs"] = [str(item) for item in refs] if isinstance(refs, list) else []

    evaluate = {"mode": "dependency_tree", "quorum": 1}
    if isinstance(guard.get("evaluate"), dict):
        evaluate.update(guard["evaluate"])
    guard["evaluate"] = evaluate

    recovery = guard.get("recovery")
    guard["recovery"] = list(recovery) if isinstance(recovery, list) else []
    guard["limits"] = guard.get("limits") if isinstance(guard.get("limits"), dict) else {
        "max_risk_level": "L1"
    }
    return guard


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_uuid(value, errors, where):
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        errors.append("%s 必须是合法 UUID：%r" % (where, value))
        return
    if parsed.version not in (4, 5):
        errors.append("%s 必须是 UUIDv4 或 UUIDv5" % where)


def validate_guards(config, errors=None, checks=None):
    """校验 guards、action_safety 与 check_refs 引用。

    checks 未显式传入时使用 config["checks"]；两者都没有时跳过引用存在性校验。
    """
    if errors is None:
        errors = []
    guards = config.get("guards")
    if not isinstance(guards, list) or not guards:
        errors.append("guards 必须是非空数组")
        return errors

    if checks is None:
        candidate = config.get("checks")
        checks = candidate if isinstance(candidate, list) else None
    available = None
    if checks is not None:
        available = {
            str(item.get("id"))
            for item in checks
            if isinstance(item, dict) and item.get("id")
        }

    seen_ids = set()
    seen_templates = set()
    for index, guard in enumerate(guards):
        where = "guards[%d]" % index
        if not isinstance(guard, dict):
            errors.append("%s 必须是对象" % where)
            continue

        _validate_uuid(guard.get("id"), errors, "%s.id" % where)
        guard_id = str(guard.get("id"))
        if guard_id in seen_ids:
            errors.append("%s.id 重复：%s" % (where, guard_id))
        seen_ids.add(guard_id)

        name = guard.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append("%s.name 必须是非空字符串" % where)

        template = guard.get("template", "")
        if template:
            if not isinstance(template, str):
                errors.append("%s.template 必须是字符串" % where)
            elif template in seen_templates:
                errors.append("%s.template 重复：%s" % (where, template))
            seen_templates.add(template)

        if not isinstance(guard.get("enabled", True), bool):
            errors.append("%s.enabled 必须是布尔值" % where)

        refs = guard.get("check_refs")
        if not isinstance(refs, list) or not refs:
            errors.append("%s.check_refs 必须是非空数组" % where)
        else:
            local_seen = set()
            for ref in refs:
                if not isinstance(ref, str) or not ref.strip():
                    errors.append("%s.check_refs 必须是字符串数组" % where)
                    continue
                if ref in local_seen:
                    errors.append("%s.check_refs 重复：%s" % (where, ref))
                local_seen.add(ref)
                if available is not None and ref not in available:
                    errors.append("%s.check_refs 引用了不存在的检测项：%s" % (where, ref))

        policy = guard.get("policy")
        if not isinstance(policy, dict):
            errors.append("%s.policy 必须是对象" % where)
        else:
            threshold = policy.get("fail_threshold", 2)
            if not _is_int(threshold) or not 1 <= threshold <= 10:
                errors.append("%s.policy.fail_threshold 必须是 1-10 的整数" % where)
            interval = policy.get("interval", 30)
            if not _is_int(interval) or not 5 <= interval <= 3600:
                errors.append("%s.policy.interval 必须是 5-3600 的整数" % where)
            if policy.get("fail_mode", "consecutive") not in ("consecutive", "window"):
                errors.append("%s.policy.fail_mode 只能是 consecutive 或 window" % where)
            window = policy.get("fail_window", 90)
            if not _is_int(window) or not 5 <= window <= 86400:
                errors.append("%s.policy.fail_window 必须是 5-86400 的整数" % where)
            success = policy.get("success_threshold", 1)
            if not _is_int(success) or not 1 <= success <= 10:
                errors.append("%s.policy.success_threshold 必须是 1-10 的整数" % where)
            cooldown = policy.get("cooldown", 300)
            if not _is_int(cooldown) or not 0 <= cooldown <= 86400:
                errors.append("%s.policy.cooldown 必须是 0-86400 的整数" % where)
            max_per_hour = policy.get("max_actions_per_hour", 3)
            if not _is_int(max_per_hour) or not 1 <= max_per_hour <= 100:
                errors.append("%s.policy.max_actions_per_hour 必须是 1-100 的整数" % where)
            gave_up = policy.get("gave_up_after", 3)
            if not _is_int(gave_up) or not 1 <= gave_up <= 20:
                errors.append("%s.policy.gave_up_after 必须是 1-20 的整数" % where)

        recovery = guard.get("recovery")
        if not isinstance(recovery, list) or not recovery:
            errors.append("%s.recovery 必须是非空数组" % where)
        else:
            for r_index, step in enumerate(recovery):
                r_where = "%s.recovery[%d]" % (where, r_index)
                if not isinstance(step, dict):
                    errors.append("%s 必须是对象" % r_where)
                    continue
                action = step.get("action")
                if action not in ALLOWED_ACTIONS:
                    errors.append("%s.action 不在动作库内：%r" % (r_where, action))
                    continue
                target = step.get("target")
                if ALLOWED_ACTIONS[action]["requires_target"]:
                    if not isinstance(target, str) or not target.strip():
                        errors.append("%s.target 必须是非空字符串" % r_where)
                elif target not in (None, ""):
                    errors.append("%s.target 必须为空" % r_where)
                verify_after = step.get("verify_after", 20)
                if not _is_int(verify_after) or not 0 <= verify_after <= 300:
                    errors.append("%s.verify_after 必须是 0-300 的整数" % r_where)
                if not isinstance(step.get("continue_on_failure", False), bool):
                    errors.append("%s.continue_on_failure 必须是布尔值" % r_where)

        limits = guard.get("limits", {})
        if not isinstance(limits, dict):
            errors.append("%s.limits 必须是对象" % where)
        elif limits.get("max_risk_level", "L1") not in ("L0", "L1"):
            errors.append("%s.limits.max_risk_level 只能是 L0 或 L1" % where)

        evaluate = guard.get("evaluate", {})
        if not isinstance(evaluate, dict):
            errors.append("%s.evaluate 必须是对象" % where)
        else:
            if evaluate.get("mode", "dependency_tree") not in (
                "dependency_tree",
                "all",
                "any",
                "quorum",
            ):
                errors.append(
                    "%s.evaluate.mode 只能是 dependency_tree/all/any/quorum" % where
                )
            quorum = evaluate.get("quorum", 1)
            if not _is_int(quorum) or not 1 <= quorum <= 100:
                errors.append("%s.evaluate.quorum 必须是 1-100 的整数" % where)

    safety = config.get("action_safety")
    if safety is not None:
        if not isinstance(safety, dict):
            errors.append("action_safety 必须是对象")
        else:
            if not isinstance(safety.get("global_enabled", True), bool):
                errors.append("action_safety.global_enabled 必须是布尔值")
            if safety.get("max_risk_level", "L1") not in ("L0", "L1"):
                errors.append("action_safety.max_risk_level 只能是 L0 或 L1")
            max_per_hour = safety.get("max_actions_per_hour", 3)
            if not _is_int(max_per_hour) or not 1 <= max_per_hour <= 100:
                errors.append("action_safety.max_actions_per_hour 必须是 1-100 的整数")
            windows = safety.get("maintenance_windows", [])
            if not isinstance(windows, list):
                errors.append("action_safety.maintenance_windows 必须是数组")
    return errors
