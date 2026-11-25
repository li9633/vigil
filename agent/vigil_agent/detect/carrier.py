"""net.carrier：WAN 物理链路载波状态检测。

直接读取 `/sys/class/net/<wan_dev>/carrier`，不依赖外部命令。
设备名通过 resolve_wan_device() 解析（uci → ubus → 显式配置）。
"""

import os

from .base import (
    STATUS_CRITICAL,
    STATUS_OK,
    STATUS_UNKNOWN,
    make_result,
    resolve_wan_device,
    truncate,
)

LAYER = "net.carrier"
DEPENDS_ON = ()


def detect(ctx):
    started = ctx.clock()
    device, source = resolve_wan_device(ctx)
    latency_ms = int((ctx.clock() - started) * 1000)

    if not device:
        return make_result(
            LAYER,
            STATUS_UNKNOWN,
            latency_ms,
            {
                "reason": "无法解析 WAN 设备（uci/ubus 均未返回）",
                "source": source,
            },
        )

    path = os.path.join(ctx.sys_class_net, device, "carrier")
    try:
        with open(path, encoding="utf-8") as handle:
            value = handle.read().strip()
    except OSError as exc:
        return make_result(
            LAYER,
            STATUS_CRITICAL,
            latency_ms,
            {
                "device": device,
                "source": source,
                "error": truncate("读取 carrier 失败：%s" % exc, 512),
            },
        )

    if value == "1":
        status = STATUS_OK
    elif value == "0":
        status = STATUS_CRITICAL
    else:
        status = STATUS_UNKNOWN

    return make_result(
        LAYER,
        status,
        latency_ms,
        {
            "device": device,
            "carrier": value,
            "source": source,
        },
    )
