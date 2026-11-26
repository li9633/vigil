"""Check（检测项）领域模型：缺省值、参数/期望校验与依赖处理。

Check 是 `probe + params + expect + timeout + depends_on` 的命名实体，
可被多个 Guard 通过 `check_refs` 引用。
"""

import copy

from ..probes.base import MAX_TIMEOUT, apply_param_defaults, validate_params
from ..utils.validation import (
    ValidationError,
    is_check_id,
    topological_sort,
)
from ..utils.validation import (
    dependency_closure as _closure,
)
from .expect import validate_expect


class CheckConfigError(ValidationError):
    """Check 配置非法。"""


def check_id(check):
    return str(check.get("id") or "")


def check_map(checks):
    """返回 {id: check}；非对象项忽略。"""
    result = {}
    for check in checks or []:
        if isinstance(check, dict) and check.get("id"):
            result[str(check["id"])] = check
    return result


def dependency_graph(checks):
    """构建 {id: depends_on} 依赖图。"""
    return {
        str(check["id"]): [str(dep) for dep in check.get("depends_on") or []]
        for check in checks or []
        if isinstance(check, dict) and check.get("id")
    }


def dependency_closure(roots, graph):
    return _closure([str(root) for root in roots or []], graph)


def ordered_checks(checks):
    """按依赖优先返回 Check 列表；存在环时抛 CheckConfigError。"""
    by_id = check_map(checks)
    graph = dependency_graph(checks)
    try:
        order = topological_sort(graph)
    except ValidationError as exc:
        raise CheckConfigError(str(exc)) from None
    return [by_id[item] for item in order if item in by_id]


def normalize_check(raw, registry):
    """补齐缺省字段并返回深拷贝；probe 不存在时抛 CheckConfigError。"""
    if not isinstance(raw, dict):
        raise CheckConfigError("检测项必须是对象")
    probe_id = str(raw.get("probe") or "").strip()
    probe = registry.get(probe_id)
    if probe is None:
        raise CheckConfigError("检测项引用了未知 probe：%r" % (raw.get("probe"),))

    cid = raw.get("id")
    if not is_check_id(cid):
        raise CheckConfigError("检测项 id 非法：%r" % (cid,))

    params = raw.get("params")
    params = dict(params) if isinstance(params, dict) else {}
    params = apply_param_defaults(probe.params_schema, params)

    expect = raw.get("expect")
    expect = copy.deepcopy(expect) if isinstance(expect, dict) else copy.deepcopy(
        probe.default_expect
    )

    timeout = raw.get("timeout", probe.default_timeout)
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = float(probe.default_timeout)

    return {
        "id": str(cid),
        "name": str(raw.get("name") or cid),
        "probe": probe_id,
        "params": params,
        "expect": expect,
        "timeout": timeout,
        "depends_on": [str(dep) for dep in raw.get("depends_on") or []],
        "enabled": bool(raw.get("enabled", True)),
    }


def normalize_checks(checks, registry):
    """规范化 Check 列表；任一非法时抛 CheckConfigError。"""
    if not isinstance(checks, list):
        raise CheckConfigError("checks 必须是数组")
    return [normalize_check(item, registry) for item in checks]


def validate_checks(checks, registry, errors=None, remote=False):
    """校验 Check 列表，把错误追加到 errors 并返回该列表。"""
    if errors is None:
        errors = []
    if not isinstance(checks, list) or not checks:
        errors.append("checks 必须是非空数组")
        return errors

    seen_ids = set()
    for index, check in enumerate(checks):
        where = "checks[%d]" % index
        if not isinstance(check, dict):
            errors.append("%s 必须是对象" % where)
            continue

        cid = check.get("id")
        if not is_check_id(cid):
            errors.append("%s.id 非法：%r" % (where, cid))
        elif str(cid) in seen_ids:
            errors.append("%s.id 重复：%s" % (where, cid))
        else:
            seen_ids.add(str(cid))

        probe_id = check.get("probe")
        probe = registry.get(probe_id) if isinstance(probe_id, str) else None
        if probe is None:
            errors.append("%s.probe 不存在：%r" % (where, probe_id))
        elif remote and not probe.remote_allowed:
            errors.append("%s.probe 不允许服务端引用：%s" % (where, probe_id))

        params = check.get("params", {})
        if not isinstance(params, dict):
            errors.append("%s.params 必须是对象" % where)
        elif probe is not None:
            for message in validate_params(probe.params_schema, params):
                errors.append("%s.params：%s" % (where, message))

        expect = check.get("expect")
        if expect is None:
            errors.append("%s.expect 不能为空" % where)
        else:
            validate_expect(expect, errors, "%s.expect" % where)

        timeout = check.get("timeout")
        if timeout is not None:
            if (
                not isinstance(timeout, (int, float))
                or isinstance(timeout, bool)
                or not 0 < float(timeout) <= MAX_TIMEOUT
            ):
                errors.append("%s.timeout 必须是 (0, 60] 的数值" % where)

        deps = check.get("depends_on", [])
        if not isinstance(deps, list) or not all(isinstance(item, str) for item in deps):
            errors.append("%s.depends_on 必须是字符串数组" % where)

        enabled = check.get("enabled", True)
        if not isinstance(enabled, bool):
            errors.append("%s.enabled 必须是布尔值" % where)

    if not errors:
        known = set(seen_ids)
        for index, check in enumerate(checks):
            where = "checks[%d]" % index
            cid = str(check.get("id"))
            for dep in check.get("depends_on") or []:
                if dep == cid:
                    errors.append("%s 不能依赖自身" % where)
                elif dep not in known:
                    errors.append("%s.depends_on 引用了不存在的检测项：%s" % (where, dep))
        if not errors:
            try:
                ordered_checks(checks)
            except CheckConfigError as exc:
                errors.append(str(exc))
    return errors
