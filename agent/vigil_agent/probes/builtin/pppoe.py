"""pppoe：通过 ubus 查询 WAN 逻辑接口状态。"""

from ..base import Observation, Probe, first_ipv4, observe_error, ubus_wan_status


class PppoeProbe(Probe):
    id = "pppoe"
    name = "PPPoE 会话"
    description = "通过 ubus 判断 WAN 逻辑接口是否 up、在线时长与地址"
    params_schema = {
        "interface": {
            "type": "string",
            "required": False,
            "default": "wan",
            "label": "逻辑接口",
        }
    }
    default_expect = {"kind": "pppoe", "up": True}
    default_timeout = 3.0
    remote_allowed = True

    def observe(self, ctx, params):
        interface = str(params.get("interface") or "wan")
        started = ctx.clock()
        data, error = ubus_wan_status(ctx, interface)
        latency = int((ctx.clock() - started) * 1000)
        if data is None:
            return observe_error(error, latency, {"interface": interface})

        try:
            uptime = int(data.get("uptime") or 0)
        except (TypeError, ValueError):
            uptime = 0
        return Observation(
            {
                "interface": interface,
                "up": bool(data.get("up")),
                "uptime": uptime,
                "proto": data.get("proto"),
                "l3_device": data.get("l3_device"),
                "ip": first_ipv4(data),
            },
            latency,
        )


PROBE = PppoeProbe()
