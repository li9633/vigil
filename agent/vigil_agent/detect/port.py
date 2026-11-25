"""net.port：TCP 端口连通性探测。

Check 参数：
- `host`：目标主机，默认 `127.0.0.1`
- `port`：目标端口（必填）

只有 TCP 三次握手成功才判定 ok；连接被拒绝、超时或解析失败均判定 critical。
"""

import socket

from .base import STATUS_CRITICAL, STATUS_OK, STATUS_UNKNOWN, make_result, truncate

LAYER = "net.port"
DEPENDS_ON = ()


def detect(ctx):
    host = str(ctx.param("host") or "127.0.0.1").strip()
    raw_port = ctx.param("port")
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        return make_result(
            LAYER, STATUS_UNKNOWN, 0, {"host": host, "reason": "缺少合法的 port 参数"}
        )
    if not 1 <= port <= 65535:
        return make_result(
            LAYER,
            STATUS_UNKNOWN,
            0,
            {"host": host, "port": port, "reason": "port 超出 1-65535"},
        )

    timeout = ctx.timeout
    started = ctx.clock()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            latency = int((ctx.clock() - started) * 1000)
            return make_result(LAYER, STATUS_OK, latency, {"host": host, "port": port})
    except socket.timeout:
        latency = int((ctx.clock() - started) * 1000)
        return make_result(
            LAYER,
            STATUS_CRITICAL,
            latency,
            {"host": host, "port": port, "reason": "连接超时"},
        )
    except OSError as exc:
        latency = int((ctx.clock() - started) * 1000)
        return make_result(
            LAYER,
            STATUS_CRITICAL,
            latency,
            {"host": host, "port": port, "reason": truncate(str(exc), 200)},
        )
