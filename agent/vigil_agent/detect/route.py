"""net.route：默认路由与出口 IP 检测。"""

import re

from .base import (
    STATUS_CRITICAL,
    STATUS_OK,
    STATUS_UNKNOWN,
    STATUS_WARN,
    make_result,
    truncate,
    ubus_wan_status,
)

LAYER = "net.route"
DEPENDS_ON = ("net.pppoe",)

# `ip -4 -o addr show` 输出中的 inet 地址
_INET_RE = re.compile(r"\binet\s+(\d{1,3}(?:\.\d{1,3}){3})")


def _first_ipv4(data):
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


def detect(ctx):
    started = ctx.clock()
    result = ctx.runner.run(["ip", "-4", "route", "show", "default"], timeout=ctx.timeout)
    latency_ms = int((ctx.clock() - started) * 1000)

    if result.timed_out:
        return make_result(
            LAYER,
            STATUS_UNKNOWN,
            latency_ms,
            {"error": "读取默认路由超时"},
        )
    if result.error or result.exit_code != 0:
        return make_result(
            LAYER,
            STATUS_UNKNOWN,
            latency_ms,
            {
                "error": truncate(result.error or result.stderr, 512)
                or ("ip 退出码 %s" % result.exit_code),
            },
        )

    route_line = ""
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("default"):
            route_line = stripped
            break

    if not route_line:
        return make_result(
            LAYER,
            STATUS_CRITICAL,
            latency_ms,
            {"reason": "未找到 IPv4 默认路由", "output": truncate(result.stdout, 512)},
        )

    data, _error, _raw = ubus_wan_status(ctx)
    egress_ip = _first_ipv4(data)

    # ubus 不可用时，尝试从路由出口设备读取地址
    if not egress_ip:
        match = re.search(r"\bdev\s+(\S+)", route_line)
        if match:
            device = match.group(1)
            addr_result = ctx.runner.run(
                ["ip", "-4", "-o", "addr", "show", "dev", device],
                timeout=ctx.timeout,
            )
            if addr_result.exit_code == 0:
                found = _INET_RE.search(addr_result.stdout)
                if found:
                    egress_ip = found.group(1)

    evidence = {
        "default_route": route_line,
        "egress_ip": egress_ip,
    }
    if not egress_ip:
        evidence["reason"] = "默认路由存在但出口 IP 为空"
        return make_result(LAYER, STATUS_WARN, latency_ms, evidence)

    return make_result(LAYER, STATUS_OK, latency_ms, evidence)
