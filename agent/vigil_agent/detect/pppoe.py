"""net.pppoe：PPPoE 会话状态检测。

只读取 `ubus call network.interface.wan status` 的 up / uptime，
不使用 `ip link`（已验证对 PPP 会话无效）。
"""

from .base import (
    STATUS_CRITICAL,
    STATUS_OK,
    STATUS_UNKNOWN,
    STATUS_WARN,
    make_result,
    truncate,
    ubus_wan_status,
)

LAYER = "net.pppoe"
DEPENDS_ON = ("net.carrier",)


def detect(ctx):
    started = ctx.clock()
    data, error, _raw = ubus_wan_status(ctx)
    latency_ms = int((ctx.clock() - started) * 1000)

    if data is None:
        return make_result(
            LAYER,
            STATUS_UNKNOWN,
            latency_ms,
            {
                "interface": ctx.wan_interface,
                "error": truncate(error, 512),
            },
        )

    up = bool(data.get("up"))
    try:
        uptime = int(data.get("uptime") or 0)
    except (TypeError, ValueError):
        uptime = 0

    if not up:
        status = STATUS_CRITICAL
    elif uptime <= 0:
        # 刚建立会话但 uptime 尚未刷新，视为告警而非故障
        status = STATUS_WARN
    else:
        status = STATUS_OK

    return make_result(
        LAYER,
        status,
        latency_ms,
        {
            "interface": ctx.wan_interface,
            "up": up,
            "uptime": uptime,
            "proto": data.get("proto"),
            "l3_device": data.get("l3_device"),
        },
    )
