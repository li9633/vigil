"""服务端内置 probe 目录与参数/期望校验。

服务端不下发、不存储 probe 可执行文件，这里只保留内置 probe 的元数据，
用于校验管理端提交的 Check 与生成 probe 下拉选项。目录需与 agent
`vigil_agent/probes/builtin` 保持一致。
"""

from __future__ import annotations

import copy
from typing import Any

# 期望类型，与 agent `checks/expect.py` 的 EXPECTATIONS 对齐
EXPECTATION_KINDS = (
    "carrier",
    "pppoe",
    "route",
    "dns",
    "http",
    "tcp",
    "process",
    "icmp",
    "command",
    "json",
)

_CHECK_ID_MAX_LEN = 64
MAX_TIMEOUT = 60.0

BUILTIN_PROBES: dict[str, dict[str, Any]] = {
    "carrier": {
        "name": "物理链路",
        "description": "读取 /sys/class/net/<device>/carrier 判断链路是否 up",
        "params_schema": {
            "device": {
                "type": "string",
                "required": False,
                "default": "auto",
                "label": "WAN 设备",
            }
        },
        "default_expect": {"kind": "carrier", "up": True},
        "default_timeout": 3.0,
        "remote_allowed": True,
    },
    "pppoe": {
        "name": "PPPoE 会话",
        "description": "通过 ubus 判断 WAN 逻辑接口是否 up、在线时长与地址",
        "params_schema": {
            "interface": {
                "type": "string",
                "required": False,
                "default": "wan",
                "label": "逻辑接口",
            }
        },
        "default_expect": {"kind": "pppoe", "up": True},
        "default_timeout": 3.0,
        "remote_allowed": True,
    },
    "route": {
        "name": "默认路由",
        "description": "检查 IPv4 默认路由与出口 IP",
        "params_schema": {
            "interface": {
                "type": "string",
                "required": False,
                "default": "wan",
                "label": "逻辑接口",
            }
        },
        "default_expect": {"kind": "route", "has_route": True},
        "default_timeout": 3.0,
        "remote_allowed": True,
    },
    "dns": {
        "name": "DNS 解析",
        "description": "经本地 DNS 解析指定域名",
        "params_schema": {
            "name": {
                "type": "string",
                "required": True,
                "label": "域名",
            },
            "server": {
                "type": "string",
                "required": False,
                "default": "127.0.0.1",
                "label": "DNS 服务器",
            },
        },
        "default_expect": {"kind": "dns", "resolved": True},
        "default_timeout": 3.0,
        "remote_allowed": True,
    },
    "http": {
        "name": "HTTP",
        "description": "请求指定 URL，按状态码 / 正文 / JSON / 时延判定",
        "params_schema": {
            "url": {
                "type": "string",
                "required": True,
                "label": "URL",
            },
            "method": {
                "type": "enum",
                "values": ["GET", "HEAD", "POST"],
                "required": False,
                "default": "GET",
                "label": "方法",
            },
            "headers": {"type": "object", "required": False, "label": "请求头"},
        },
        "default_expect": {"kind": "http", "status": [200, 204]},
        "default_timeout": 5.0,
        "remote_allowed": True,
    },
    "tcp": {
        "name": "TCP 端口",
        "description": "TCP 连接目标主机与端口",
        "params_schema": {
            "host": {
                "type": "string",
                "required": True,
                "default": "127.0.0.1",
                "label": "主机",
            },
            "port": {"type": "integer", "required": True, "label": "端口"},
        },
        "default_expect": {"kind": "tcp", "connected": True},
        "default_timeout": 3.0,
        "remote_allowed": True,
    },
    "process": {
        "name": "进程/服务",
        "description": "查询 procd 服务状态，ubus 不可用时回退 pgrep",
        "params_schema": {
            "service": {"type": "string", "required": False, "label": "procd 服务"},
            "process": {"type": "string", "required": False, "label": "进程匹配"},
        },
        "default_expect": {"kind": "process", "running": True},
        "default_timeout": 3.0,
        "remote_allowed": True,
    },
    "icmp": {
        "name": "ICMP",
        "description": "ping 目标地址，观测可达性与丢包/时延",
        "params_schema": {
            "host": {"type": "string", "required": True, "label": "目标主机"},
            "count": {
                "type": "integer",
                "required": False,
                "default": 1,
                "label": "次数",
            },
        },
        "default_expect": {"kind": "icmp", "reachable": True},
        "default_timeout": 3.0,
        "remote_allowed": True,
    },
    "command": {
        "name": "本地命令",
        "description": "执行固定 argv 并按退出码 / 输出判定（仅本地可配置）",
        "params_schema": {
            "argv": {
                "type": "array",
                "items": "string",
                "required": True,
                "label": "命令参数",
            },
            "cwd": {"type": "string", "required": False, "label": "工作目录"},
        },
        "default_expect": {"kind": "command", "exit_code": 0},
        "default_timeout": 10.0,
        "remote_allowed": False,
    },
}


def builtin_probe(probe_id: str) -> dict[str, Any] | None:
    entry = BUILTIN_PROBES.get(str(probe_id or ""))
    return copy.deepcopy(entry) if entry else None


def catalog_entries() -> list[dict[str, Any]]:
    """返回服务端内置 probe 目录（供管理端下拉使用）。"""
    items: list[dict[str, Any]] = []
    for probe_id, entry in BUILTIN_PROBES.items():
        items.append(
            {
                "id": probe_id,
                "name": entry["name"],
                "description": entry["description"],
                "version": "builtin",
                "source": "builtin",
                "remote_allowed": bool(entry["remote_allowed"]),
                "params_schema": copy.deepcopy(entry["params_schema"]),
                "default_expect": copy.deepcopy(entry["default_expect"]),
                "default_timeout": float(entry["default_timeout"]),
            }
        )
    return items


def _type_error(name: str, expected: str, value: Any) -> str:
    return f"参数 {name} 必须是{expected}，实际为 {value!r}"


def validate_params(schema: dict[str, Any], params: dict[str, Any]) -> list[str]:
    """按 params_schema 校验参数，返回错误列表。"""
    errors: list[str] = []
    for name, spec in (schema or {}).items():
        if not isinstance(spec, dict):
            continue
        value = params.get(name)
        if value is None:
            if spec.get("required", False):
                errors.append(f"缺少必填参数：{name}")
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
                errors.append(f"参数 {name} 必须是 {list(values)!r} 之一")
        elif kind == "array":
            if not isinstance(value, list):
                errors.append(_type_error(name, "数组", value))
            elif spec.get("items") == "string" and not all(
                isinstance(item, str) for item in value
            ):
                errors.append(f"参数 {name} 必须是字符串数组")
        elif kind == "object":
            if not isinstance(value, dict):
                errors.append(_type_error(name, "对象", value))
    return errors


def apply_param_defaults(schema: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """把 params_schema 中的 default 填入缺失参数（返回新字典）。"""
    merged = dict(params or {})
    for name, spec in (schema or {}).items():
        if isinstance(spec, dict) and name not in merged and "default" in spec:
            merged[name] = copy.deepcopy(spec["default"])
    return merged


_BOOL_FIELDS = ("up", "require_ip", "has_route", "resolved", "connected", "running", "reachable")
_STRING_FIELDS = ("body_contains", "stdout_contains", "contains", "path")
_NUMBER_FIELDS = ("max_latency_ms", "max_loss")


def validate_expect(expect: Any) -> list[str]:
    """校验 Expectation 结构、kind 与字段类型。"""
    if not isinstance(expect, dict):
        return ["expect 必须是对象"]
    kind = expect.get("kind")
    if kind not in EXPECTATION_KINDS:
        return [f"expect.kind 必须是已注册类型：{kind!r}"]

    errors: list[str] = []
    for field in _BOOL_FIELDS:
        if field in expect and not isinstance(expect[field], bool):
            errors.append(f"expect.{field} 必须是布尔值")
    for field in _STRING_FIELDS:
        if field in expect and not isinstance(expect[field], str):
            errors.append(f"expect.{field} 必须是字符串")
    for field in _NUMBER_FIELDS:
        if field in expect:
            value = expect[field]
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                errors.append(f"expect.{field} 必须是非负数值")

    status = expect.get("status")
    if status is not None:
        items = status if isinstance(status, list) else [status]
        if not all(isinstance(item, int) and not isinstance(item, bool) for item in items):
            errors.append("expect.status 必须是整数或整数数组")
    exit_code = expect.get("exit_code")
    if exit_code is not None:
        items = exit_code if isinstance(exit_code, list) else [exit_code]
        if not all(isinstance(item, int) and not isinstance(item, bool) for item in items):
            errors.append("expect.exit_code 必须是整数或整数数组")
    if "egress_ip" in expect and not isinstance(expect["egress_ip"], (str, list)):
        errors.append("expect.egress_ip 必须是字符串或字符串数组")
    if "json" in expect and expect["json"] is not None and not isinstance(expect["json"], dict):
        errors.append("expect.json 必须是对象")
    return errors
