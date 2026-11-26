"""Expectation：按 `kind` 把 Observation 判定为 ok / critical / unknown。

评估器统一返回 (status, evidence)，evidence 至少包含 observed / expected / reason。
V2 不使用 warn。
"""

import json

STATUS_OK = "ok"
STATUS_CRITICAL = "critical"
STATUS_UNKNOWN = "unknown"

_MISSING = object()


def _result(status, observed, expected, reason, **extra):
    evidence = {"observed": observed, "expected": expected, "reason": reason}
    evidence.update(extra)
    return status, evidence


def _unknown(observation, expected, reason):
    return _result(STATUS_UNKNOWN, observation.data, expected, reason)


def _as_list(value):
    if isinstance(value, list):
        return value
    return [value]


def _json_path(data, path):
    """按 a.b.0.c 形式取值；缺失返回 _MISSING。"""
    current = data
    for part in str(path or "").split("."):
        if part == "":
            continue
        if isinstance(current, dict):
            if part not in current:
                return _MISSING
            current = current[part]
        elif isinstance(current, list):
            try:
                index = int(part)
            except ValueError:
                return _MISSING
            if not 0 <= index < len(current):
                return _MISSING
            current = current[index]
        else:
            return _MISSING
    return current


def _observation_failed(observation, expected, reason="观察失败"):
    return _unknown(observation, expected, observation.error or reason)


def _carrier(observation, expect):
    data = observation.data
    if observation.error and "up" not in data:
        return _observation_failed(observation, expect)
    up = data.get("up")
    if up is True:
        return _result(STATUS_OK, data, expect, "carrier_up")
    if up is False:
        return _result(STATUS_CRITICAL, data, expect, "carrier_down")
    return _unknown(observation, expect, "carrier_unknown")


def _pppoe(observation, expect):
    if observation.error:
        return _observation_failed(observation, expect)
    data = observation.data
    up = data.get("up")
    if up is not True:
        return _result(STATUS_CRITICAL, data, expect, "pppoe_down")
    if expect.get("require_ip") and not data.get("ip"):
        return _result(STATUS_CRITICAL, data, expect, "pppoe_no_ip")
    return _result(STATUS_OK, data, expect, "pppoe_up")


def _route(observation, expect):
    if observation.error:
        return _observation_failed(observation, expect)
    data = observation.data
    if data.get("has_route") is not True:
        return _result(STATUS_CRITICAL, data, expect, "no_default_route")
    wanted = expect.get("egress_ip")
    if wanted:
        allowed = [str(item) for item in _as_list(wanted)]
        if str(data.get("egress_ip") or "") not in allowed:
            return _result(STATUS_CRITICAL, data, expect, "egress_ip_mismatch")
    return _result(STATUS_OK, data, expect, "route_ok")


def _dns(observation, expect):
    if observation.error:
        return _observation_failed(observation, expect)
    data = observation.data
    if data.get("resolved") is not True:
        return _result(STATUS_CRITICAL, data, expect, "dns_not_resolved")
    contains = expect.get("contains")
    if contains:
        addresses = [str(item) for item in data.get("addresses") or []]
        if not any(str(contains) in item for item in addresses):
            return _result(STATUS_CRITICAL, data, expect, "dns_address_mismatch")
    return _result(STATUS_OK, data, expect, "dns_resolved")


def _http(observation, expect):
    data = observation.data
    status = int(data.get("status") or 0)
    if observation.error and status == 0:
        return _result(STATUS_CRITICAL, _http_observed(data), expect, "request_failed")
    allowed = [int(item) for item in _as_list(expect.get("status", [200, 204]))]
    if status not in allowed:
        return _result(
            STATUS_CRITICAL, _http_observed(data), expect, "status_not_expected"
        )

    body = str(data.get("body") or "")
    body_contains = expect.get("body_contains")
    if body_contains and str(body_contains) not in body:
        return _result(
            STATUS_CRITICAL, _http_observed(data), expect, "body_mismatch"
        )

    json_expect = expect.get("json")
    if isinstance(json_expect, dict):
        try:
            parsed = json.loads(body)
        except ValueError:
            return _result(
                STATUS_CRITICAL, _http_observed(data), expect, "body_not_json"
            )
        reason = _json_assert(parsed, json_expect)
        if reason:
            return _result(STATUS_CRITICAL, _http_observed(data), expect, reason)

    max_latency = expect.get("max_latency_ms")
    if max_latency is not None and observation.latency_ms > int(max_latency):
        return _result(
            STATUS_CRITICAL, _http_observed(data), expect, "latency_exceeded"
        )
    return _result(STATUS_OK, _http_observed(data), expect, "http_ok")


def _http_observed(data):
    body = str(data.get("body") or "")
    return {
        "status": data.get("status"),
        "body_head": body[:200],
        "body_length": len(body),
    }


def _json_assert(value, expect):
    if "path" in expect:
        found = _json_path(value, expect.get("path"))
        if found is _MISSING:
            if expect.get("exists") is False:
                return ""
            return "json_path_missing"
        if expect.get("exists") is False:
            return "json_path_exists"
        if "equals" in expect and found != expect["equals"]:
            return "json_value_mismatch"
        return ""
    if "equals" in expect and value != expect["equals"]:
        return "json_value_mismatch"
    return ""


def _tcp(observation, expect):
    data = observation.data
    if data.get("connected") is not True:
        return _result(STATUS_CRITICAL, data, expect, "tcp_not_connected")
    max_latency = expect.get("max_latency_ms")
    if max_latency is not None and observation.latency_ms > int(max_latency):
        return _result(STATUS_CRITICAL, data, expect, "latency_exceeded")
    return _result(STATUS_OK, data, expect, "tcp_connected")


def _process(observation, expect):
    data = observation.data
    if observation.error:
        return _observation_failed(observation, expect)
    if data.get("running") is True:
        return _result(STATUS_OK, data, expect, "process_running")
    if data.get("running") is False:
        return _result(STATUS_CRITICAL, data, expect, "process_not_running")
    return _unknown(observation, expect, "process_unknown")


def _icmp(observation, expect):
    if observation.error:
        return _observation_failed(observation, expect)
    data = observation.data
    if data.get("reachable") is not True:
        return _result(STATUS_CRITICAL, data, expect, "icmp_unreachable")
    max_loss = expect.get("max_loss")
    loss = data.get("loss_percent")
    if max_loss is not None and loss is not None and loss > float(max_loss):
        return _result(STATUS_CRITICAL, data, expect, "packet_loss_exceeded")
    max_latency = expect.get("max_latency_ms")
    rtt = data.get("rtt_ms")
    if max_latency is not None and rtt is not None and rtt > float(max_latency):
        return _result(STATUS_CRITICAL, data, expect, "latency_exceeded")
    return _result(STATUS_OK, data, expect, "icmp_reachable")


def _command(observation, expect):
    if observation.error:
        return _observation_failed(observation, expect)
    data = observation.data
    allowed = [int(item) for item in _as_list(expect.get("exit_code", 0))]
    exit_code = data.get("exit_code")
    if exit_code not in allowed:
        return _result(STATUS_CRITICAL, data, expect, "exit_code_mismatch")
    stdout_contains = expect.get("stdout_contains")
    if stdout_contains and str(stdout_contains) not in str(data.get("stdout") or ""):
        return _result(STATUS_CRITICAL, data, expect, "stdout_mismatch")
    return _result(STATUS_OK, data, expect, "command_ok")


def _json(observation, expect):
    if observation.error:
        return _observation_failed(observation, expect)
    reason = _json_assert(observation.data, expect)
    if reason:
        return _result(STATUS_CRITICAL, observation.data, expect, reason)
    return _result(STATUS_OK, observation.data, expect, "json_ok")


EXPECTATIONS = {
    "carrier": _carrier,
    "pppoe": _pppoe,
    "route": _route,
    "dns": _dns,
    "http": _http,
    "tcp": _tcp,
    "process": _process,
    "icmp": _icmp,
    "command": _command,
    "json": _json,
}

_BOOL_FIELDS = ("up", "require_ip", "has_route", "resolved", "connected", "running", "reachable")
_STRING_FIELDS = ("body_contains", "stdout_contains", "contains", "path")
_NUMBER_FIELDS = ("max_latency_ms", "max_loss")


def validate_expect(expect, errors, where="expect"):
    """校验 Expectation 结构与字段类型。"""
    if not isinstance(expect, dict):
        errors.append("%s 必须是对象" % where)
        return
    kind = expect.get("kind")
    if kind not in EXPECTATIONS:
        errors.append("%s.kind 必须是已注册类型：%r" % (where, kind))
        return
    for field in _BOOL_FIELDS:
        if field in expect and not isinstance(expect[field], bool):
            errors.append("%s.%s 必须是布尔值" % (where, field))
    for field in _STRING_FIELDS:
        if field in expect and not isinstance(expect[field], str):
            errors.append("%s.%s 必须是字符串" % (where, field))
    for field in _NUMBER_FIELDS:
        if field in expect:
            value = expect[field]
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                errors.append("%s.%s 必须是非负数值" % (where, field))

    status = expect.get("status")
    if status is not None:
        items = status if isinstance(status, list) else [status]
        if not all(isinstance(item, int) and not isinstance(item, bool) for item in items):
            errors.append("%s.status 必须是整数或整数数组" % where)
    exit_code = expect.get("exit_code")
    if exit_code is not None:
        items = exit_code if isinstance(exit_code, list) else [exit_code]
        if not all(isinstance(item, int) and not isinstance(item, bool) for item in items):
            errors.append("%s.exit_code 必须是整数或整数数组" % where)
    if "egress_ip" in expect and not isinstance(expect["egress_ip"], (str, list)):
        errors.append("%s.egress_ip 必须是字符串或字符串数组" % where)
    if "json" in expect and expect["json"] is not None and not isinstance(expect["json"], dict):
        errors.append("%s.json 必须是对象" % where)


def evaluate(expect, observation):
    """执行 Expectation，返回 (status, evidence)。"""
    expect = expect if isinstance(expect, dict) else {}
    kind = expect.get("kind")
    evaluator = EXPECTATIONS.get(kind)
    if evaluator is None:
        return (
            STATUS_UNKNOWN,
            {
                "observed": observation.data,
                "expected": expect,
                "reason": "unknown_expectation",
            },
        )
    return evaluator(observation, expect)
