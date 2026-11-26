"""Probe 基础设施：Observation、Probe、注册表与参数校验。

Probe 只负责观测，不返回 ok / critical；判定由 `checks.expect` 完成。
每个 Probe 通过类属性声明参数与默认期望，服务端与本地 UI 共用同一份目录。
"""

import json
import time
from typing import Any

from ..utils.proc import CommandRunner
from ..utils.text import truncate

PARAM_TYPES = ("string", "number", "integer", "boolean", "enum", "array", "object")

DEFAULT_TIMEOUT = 5.0
MAX_TIMEOUT = 60.0


class Observation:
    """一次观测结果：`data` 必须是 JSON 对象，`error` 非空表示观测失败。"""

    __slots__ = ("data", "latency_ms", "error")

    def __init__(self, data=None, latency_ms=0, error=""):
        self.data = dict(data) if isinstance(data, dict) else {}
        self.latency_ms = int(latency_ms or 0)
        self.error = str(error or "")

    def to_dict(self):
        return {
            "data": self.data,
            "latency_ms": self.latency_ms,
            "error": self.error or None,
        }

    def __repr__(self):
        return "Observation(latency_ms=%r, error=%r)" % (self.latency_ms, self.error)


def observe_error(error, latency_ms=0, data=None):
    """构造观测失败结果。"""
    return Observation(data or {}, latency_ms, error)


class Probe:
    """探测器基类：子类只实现 `observe`，并声明元信息。"""

    id = ""
    name = ""
    description = ""
    params_schema: dict[str, dict[str, Any]] = {}
    default_expect: dict[str, Any] = {}
    default_timeout = DEFAULT_TIMEOUT
    remote_allowed = True
    source = "builtin"
    version = "builtin"

    def observe(self, ctx, params):
        raise NotImplementedError

    def catalog_entry(self):
        return {
            "id": self.id,
            "name": self.name or self.id,
            "description": self.description,
            "version": self.version,
            "source": self.source,
            "remote_allowed": bool(self.remote_allowed),
            "params_schema": dict(self.params_schema),
            "default_expect": dict(self.default_expect),
            "default_timeout": float(self.default_timeout),
        }


class ProbeRegistry:
    """按 id 管理 probe；内置与本地 probe 共用同一注册表。"""

    def __init__(self, probes=()):
        self._probes = {}
        for probe in probes:
            self.register(probe)

    def register(self, probe):
        probe_id = str(getattr(probe, "id", "") or "").strip()
        if not probe_id:
            raise ValueError("probe 缺少 id")
        if not callable(getattr(probe, "observe", None)):
            raise TypeError("probe 必须实现 observe：%s" % probe_id)
        if probe_id in self._probes:
            raise ValueError("probe 重复注册：%s" % probe_id)
        self._probes[probe_id] = probe
        return self

    def get(self, probe_id):
        return self._probes.get(str(probe_id or ""))

    def ids(self):
        return list(self._probes.keys())

    def catalog(self):
        return [self._probes[key].catalog_entry() for key in self._probes]

    def remote_ids(self):
        return [key for key, probe in self._probes.items() if probe.remote_allowed]


class DetectContext:
    """一次检测周期共享的上下文。

    - runner: 命令执行器（可注入），需实现 run(args, timeout, env, cwd)
    - sys_class_net: carrier 文件根目录（测试可指向临时目录）
    - http_get: HTTP 探测函数 (url, timeout, method, headers) -> (status, body, latency, error)
    - cache: 一轮内共享缓存（例如 ubus 状态只取一次）
    """

    def __init__(
        self,
        config=None,
        runner=None,
        sys_class_net="/sys/class/net",
        http_get=None,
        clock=None,
        params=None,
        timeout=None,
        cache=None,
    ):
        self.config = config if isinstance(config, dict) else {}
        self.runner = runner or CommandRunner()
        self.sys_class_net = sys_class_net
        self.http_get = http_get
        self.clock = clock or time.monotonic
        self.params = params if isinstance(params, dict) else {}
        self.timeout = float(timeout if timeout is not None else DEFAULT_TIMEOUT)
        self.cache = cache if cache is not None else {}

    def run(self, args, timeout=None, env=None, cwd=None):
        return self.runner.run(args, timeout=timeout or self.timeout, env=env, cwd=cwd)

    def param(self, key, default=None):
        if key in self.params and self.params[key] is not None:
            return self.params[key]
        return default


def ubus_wan_status(ctx, interface="wan"):
    """读取 `ubus call network.interface.<iface> status`。

    返回 (data, error)；结果按接口缓存，一轮内多个 Check 共享。
    """
    key = ("ubus.wan_status", str(interface))
    if key in ctx.cache:
        return ctx.cache[key]

    target = "network.interface.%s" % interface
    result = ctx.run(["ubus", "call", target, "status"])
    data = None
    error = ""
    if result.timed_out:
        error = "ubus 调用超时"
    elif result.error:
        error = truncate(result.error)
    elif result.exit_code != 0:
        error = truncate(result.stderr) or ("ubus 退出码 %s" % result.exit_code)
    elif result.stdout.strip():
        try:
            parsed = json.loads(result.stdout)
            if isinstance(parsed, dict):
                data = parsed
            else:
                error = "ubus 返回非对象"
        except ValueError:
            error = "ubus 返回非 JSON"
    else:
        error = "ubus 无输出"

    ctx.cache[key] = (data, error)
    return data, error


def first_ipv4(data):
    """从 ubus status 中提取第一个 IPv4 地址。"""
    if not data:
        return ""
    for item in data.get("ipv4-address") or []:
        if isinstance(item, dict):
            address = item.get("address")
            if isinstance(address, str) and address:
                return address.split("/")[0]
    for item in data.get("addresses") or []:
        if isinstance(item, dict) and item.get("family") == "ipv4":
            address = item.get("address")
            if isinstance(address, str) and address:
                return address.split("/")[0]
    return ""


def resolve_wan_device(ctx, configured="auto"):
    """解析 WAN 物理设备名，返回 (device, source)。"""
    configured = str(configured or "auto").strip()
    key = ("wan.device", configured)
    if key in ctx.cache:
        return ctx.cache[key]

    if configured and configured != "auto":
        ctx.cache[key] = (configured, "config")
        return ctx.cache[key]

    result = ctx.run(["uci", "get", "network.wan.device"])
    if result.exit_code == 0:
        lines = result.stdout.strip().splitlines()
        device = lines[0].strip() if lines else ""
        if device and device != "auto":
            ctx.cache[key] = (device, "uci")
            return ctx.cache[key]

    data, _error = ubus_wan_status(ctx)
    if data:
        for field in ("l3_device", "device"):
            device = data.get(field)
            if isinstance(device, str) and device.strip():
                ctx.cache[key] = (device.strip(), "ubus")
                return ctx.cache[key]

    ctx.cache[key] = (None, "unresolved")
    return ctx.cache[key]


def _type_error(name, expected, value):
    return "参数 %s 必须是 %s，实际为 %r" % (name, expected, value)


def validate_params(schema, params):
    """按 params_schema 校验参数，返回错误列表。"""
    if not isinstance(params, dict):
        return ["params 必须是对象"]
    schema = schema if isinstance(schema, dict) else {}
    errors = []
    for name, spec in schema.items():
        if not isinstance(spec, dict):
            continue
        value = params.get(name)
        required = bool(spec.get("required", False))
        if value is None:
            if required:
                errors.append("缺少必填参数：%s" % name)
            continue
        kind = spec.get("type", "string")
        if kind == "string":
            if not isinstance(value, str):
                errors.append(_type_error(name, "字符串", value))
        elif kind == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                errors.append(_type_error(name, "数值", value))
        elif kind == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                errors.append(_type_error(name, "整数", value))
        elif kind == "boolean":
            if not isinstance(value, bool):
                errors.append(_type_error(name, "布尔值", value))
        elif kind == "enum":
            values = spec.get("values") or spec.get("enum") or []
            if value not in values:
                errors.append("参数 %s 必须是 %r 之一" % (name, list(values)))
        elif kind == "array":
            if not isinstance(value, list):
                errors.append(_type_error(name, "数组", value))
            elif spec.get("items") == "string" and not all(
                isinstance(item, str) for item in value
            ):
                errors.append("参数 %s 必须是字符串数组" % name)
        elif kind == "object":
            if not isinstance(value, dict):
                errors.append(_type_error(name, "对象", value))
    return errors


def apply_param_defaults(schema, params):
    """把 params_schema 中的 default 填入缺失参数（返回新字典）。"""
    merged = dict(params or {})
    for name, spec in (schema or {}).items():
        if not isinstance(spec, dict) or name in merged:
            continue
        if "default" in spec:
            merged[name] = spec["default"]
    return merged
