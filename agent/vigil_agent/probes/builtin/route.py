"""route：默认路由与出口 IP 观测。"""

import re

from ..base import Observation, Probe, first_ipv4, observe_error, truncate, ubus_wan_status

_INET_RE = re.compile(r"\binet\s+(\d{1,3}(?:\.\d{1,3}){3})")


class RouteProbe(Probe):
    id = "route"
    name = "默认路由"
    description = "检查 IPv4 默认路由与出口 IP"
    params_schema = {
        "interface": {
            "type": "string",
            "required": False,
            "default": "wan",
            "label": "逻辑接口",
        }
    }
    default_expect = {"kind": "route", "has_route": True}
    default_timeout = 3.0
    remote_allowed = True

    def observe(self, ctx, params):
        interface = str(params.get("interface") or "wan")
        started = ctx.clock()
        result = ctx.run(["ip", "-4", "route", "show", "default"])
        latency = int((ctx.clock() - started) * 1000)

        if result.timed_out:
            return observe_error("读取默认路由超时", latency)
        if result.error or result.exit_code != 0:
            detail = result.error or result.stderr
            return observe_error(
                truncate(detail) or ("ip 退出码 %s" % result.exit_code), latency
            )

        route_line = ""
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("default"):
                route_line = stripped
                break
        if not route_line:
            return Observation(
                {"has_route": False, "default_route": "", "egress_ip": ""}, latency
            )

        data, _error = ubus_wan_status(ctx, interface)
        egress_ip = first_ipv4(data)
        if not egress_ip:
            match = re.search(r"\bdev\s+(\S+)", route_line)
            if match:
                device = match.group(1)
                addr_result = ctx.run(["ip", "-4", "-o", "addr", "show", "dev", device])
                if addr_result.exit_code == 0:
                    found = _INET_RE.search(addr_result.stdout)
                    if found:
                        egress_ip = found.group(1)

        return Observation(
            {
                "has_route": True,
                "default_route": route_line,
                "egress_ip": egress_ip,
            },
            int((ctx.clock() - started) * 1000),
        )


PROBE = RouteProbe()
