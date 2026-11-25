"""Guard（守护项）配置模型与 v1→v2 迁移。

Guard 是用户可配置的完整监控单元：基本信息 + 检测条件 + 触发策略 + 恢复动作。
配置层只负责数据结构、缺省值与校验，检测与状态机分别由 probes / guards.runner 实现。
"""

import copy
import uuid

# 内置网络守护项的固定 UUID，保证同一客户端升级后引用不变
BUILTIN_NETWORK_ID = "6f2c1a4e-9b7d-4c3a-8e51-2d4b6a8f0c37"
BUILTIN_NETWORK_TEMPLATE = "network.up"

# 触发策略缺省值，对应设计文档 13 章
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

# 动作库白名单，v1 只允许 L1
ALLOWED_ACTIONS = {
    "ifup_wan": {"requires_target": False, "risk": "L1"},
    "restart_service": {"requires_target": True, "risk": "L1"},
}

KNOWN_PROBES = {"carrier", "pppoe", "route", "dns", "http", "process", "port", "script"}


class GuardConfigError(Exception):
    """Guard 配置非法。"""


def build_network_guard(config):
    """按旧 detect/action 配置生成内置网络守护项。"""
    detect = config.get("detect") or {}
    action = config.get("action") or {}
    timeout = detect.get("timeout", 3)
    urls = detect.get("http_probes") or ["http://connect.rom.miui.com/generate_204"]

    policy = copy.deepcopy(DEFAULT_POLICY)
    policy["interval"] = int(detect.get("interval", policy["interval"]))
    policy["fail_threshold"] = int(action.get("failure_threshold", policy["fail_threshold"]))
    policy["cooldown"] = int(action.get("cooldown_seconds", policy["cooldown"]))
    policy["max_actions_per_hour"] = int(action.get("max_per_hour", policy["max_actions_per_hour"]))

    return {
        "id": BUILTIN_NETWORK_ID,
        "name": "断网自动尝试恢复",
        "template": BUILTIN_NETWORK_TEMPLATE,
        "enabled": True,
        "checks": [
            {
                "probe": "carrier",
                "params": {"device": detect.get("wan_device", "auto")},
                "role": "root",
                "timeout": timeout,
            },
            {
                "probe": "pppoe",
                "params": {"interface": detect.get("wan_interface", "wan")},
                "depends_on": ["carrier"],
                "timeout": timeout,
            },
            {"probe": "route", "depends_on": ["pppoe"], "timeout": timeout},
            {
                "probe": "dns",
                "params": {"name": detect.get("dns_probe", "www.baidu.com")},
                "depends_on": ["route"],
                "timeout": timeout,
            },
            {
                "probe": "http",
                "params": {"url": list(urls)},
                "depends_on": ["route"],
                "timeout": timeout,
            },
        ],
        "policy": policy,
        "recovery": [
            {"action": "ifup_wan", "verify_after": 20, "continue_on_failure": False}
        ],
        "limits": {"max_risk_level": "L1"},
    }


def _normalize_policy(raw):
    policy = copy.deepcopy(DEFAULT_POLICY)
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key in policy and value is not None:
                policy[key] = value
    return policy


def normalize_guard(raw, base_config=None):
    """补齐缺省字段，返回深拷贝；不修改入参。"""
    if not isinstance(raw, dict):
        raise GuardConfigError("Guard 必须是对象")
    guard = copy.deepcopy(raw)

    if not guard.get("id"):
        guard["id"] = str(uuid.uuid4())
    if not guard.get("name"):
        guard["name"] = "未命名守护项"
    guard.setdefault("template", "")
    guard.setdefault("enabled", True)
    guard["policy"] = _normalize_policy(guard.get("policy"))
    guard.setdefault("checks", [])
    guard.setdefault("recovery", [])
    guard.setdefault("limits", {"max_risk_level": "L1"})
    evaluate = {"mode": "dependency_tree", "quorum": 1}
    if isinstance(guard.get("evaluate"), dict):
        evaluate.update(guard["evaluate"])
    guard["evaluate"] = evaluate

    if not guard["checks"] and guard.get("template") == BUILTIN_NETWORK_TEMPLATE:
        guard["checks"] = build_network_guard(base_config or {})["checks"]
    if not guard["recovery"]:
        guard["recovery"] = [
            {"action": "ifup_wan", "verify_after": 20, "continue_on_failure": False}
        ]
    return guard


def migrate_config(config):
    """就地迁移到 schema 2，返回同一对象。

    - 已有 guards 的配置只做补齐
    - schema 1 或缺少 guards 时，由 detect/action 生成内置网络守护项
    - action_safety 由旧的 action 安全上限映射
    """
    if not isinstance(config, dict):
        raise GuardConfigError("配置根节点必须是对象")

    was_v1 = config.get("schema_version") != 2
    guards = config.get("guards")
    if not isinstance(guards, list) or not guards:
        guards = [build_network_guard(config)]
    config["guards"] = [normalize_guard(item, config) for item in guards]

    safety = config.get("action_safety")
    if was_v1 or not isinstance(safety, dict):
        action = config.get("action") or {}
        safety = copy.deepcopy(DEFAULT_ACTION_SAFETY)
        safety["global_enabled"] = bool(action.get("enabled", True))
        safety["max_risk_level"] = str(action.get("max_level", "L1"))
        safety["max_actions_per_hour"] = int(
            action.get("max_per_hour", safety["max_actions_per_hour"])
        )
        config["action_safety"] = safety
    else:
        merged = copy.deepcopy(DEFAULT_ACTION_SAFETY)
        merged.update(safety)
        config["action_safety"] = merged

    config["schema_version"] = 2
    return config


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


def validate_guards(config, errors=None):
    """校验 guards 与 action_safety，把错误追加到 errors 并返回该列表。

    action_safety 缺省时跳过，便于只校验服务端/本地下发的 guards 片段。
    """
    if errors is None:
        errors = []
    guards = config.get("guards")
    if not isinstance(guards, list) or not guards:
        errors.append("guards 必须是非空数组")
        return errors

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

        checks = guard.get("checks")
        if not isinstance(checks, list) or not checks:
            errors.append("%s.checks 必须是非空数组" % where)
        else:
            check_ids = set()
            for c_index, check in enumerate(checks):
                c_where = "%s.checks[%d]" % (where, c_index)
                if not isinstance(check, dict):
                    errors.append("%s 必须是对象" % c_where)
                    continue
                probe = check.get("probe")
                if not isinstance(probe, str) or not probe.strip():
                    errors.append("%s.probe 必须是非空字符串" % c_where)
                params = check.get("params", {})
                if not isinstance(params, dict):
                    errors.append("%s.params 必须是对象" % c_where)
                timeout = check.get("timeout")
                if timeout is not None and (
                    not _is_number(timeout) or not 0 < float(timeout) <= 60
                ):
                    errors.append("%s.timeout 必须是 (0, 60] 的数值" % c_where)
                check_id = check.get("id") or probe
                if not isinstance(check_id, str):
                    errors.append("%s.id 必须是字符串" % c_where)
                elif check_id in check_ids:
                    errors.append("%s.id 重复：%s" % (c_where, check_id))
                else:
                    check_ids.add(check_id)
                deps = check.get("depends_on", [])
                if not isinstance(deps, list) or not all(
                    isinstance(item, str) for item in deps
                ):
                    errors.append("%s.depends_on 必须是字符串数组" % c_where)

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
