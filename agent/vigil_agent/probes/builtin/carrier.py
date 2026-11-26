"""carrier：读取 `/sys/class/net/<device>/carrier` 判断 WAN 物理链路。"""

import os

from ..base import Observation, Probe, observe_error, resolve_wan_device


class CarrierProbe(Probe):
    id = "carrier"
    name = "物理链路"
    description = "读取 /sys/class/net/<device>/carrier 判断链路是否 up"
    params_schema = {
        "device": {
            "type": "string",
            "required": False,
            "default": "auto",
            "label": "WAN 设备",
            "placeholder": "auto",
        }
    }
    default_expect = {"kind": "carrier", "up": True}
    default_timeout = 3.0
    remote_allowed = True

    def observe(self, ctx, params):
        started = ctx.clock()
        device, source = resolve_wan_device(ctx, params.get("device", "auto"))
        latency = int((ctx.clock() - started) * 1000)
        if not device:
            return observe_error(
                "无法解析 WAN 设备（uci/ubus 均未返回）",
                latency,
                {"device": None, "source": source},
            )

        path = os.path.join(ctx.sys_class_net, device, "carrier")
        try:
            with open(path, encoding="utf-8") as handle:
                value = handle.read().strip()
        except OSError as exc:
            return observe_error(
                "读取 carrier 失败：%s" % exc,
                latency,
                {"device": device, "source": source},
            )

        data = {
            "device": device,
            "source": source,
            "carrier": value,
            "up": value == "1" if value in ("0", "1") else None,
        }
        error = "" if value in ("0", "1") else "carrier 值异常：%s" % value
        return Observation(data, int((ctx.clock() - started) * 1000), error)


PROBE = CarrierProbe()
