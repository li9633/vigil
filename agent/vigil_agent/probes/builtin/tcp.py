"""tcp：TCP 端口连通性观测。"""

import socket

from ..base import Observation, Probe, observe_error, truncate


class TcpProbe(Probe):
    id = "tcp"
    name = "TCP 端口"
    description = "TCP 连接目标主机与端口"
    params_schema = {
        "host": {
            "type": "string",
            "required": True,
            "default": "127.0.0.1",
            "label": "主机",
        },
        "port": {"type": "integer", "required": True, "label": "端口"},
    }
    default_expect = {"kind": "tcp", "connected": True}
    default_timeout = 3.0
    remote_allowed = True

    def observe(self, ctx, params):
        host = str(params.get("host") or "127.0.0.1").strip()
        try:
            port = int(params.get("port"))
        except (TypeError, ValueError):
            return observe_error("缺少合法的 port 参数", 0, {"host": host})
        if not 1 <= port <= 65535:
            return observe_error(
                "port 超出 1-65535", 0, {"host": host, "port": port}
            )

        started = ctx.clock()
        try:
            with socket.create_connection((host, port), timeout=ctx.timeout):
                latency = int((ctx.clock() - started) * 1000)
                return Observation(
                    {"host": host, "port": port, "connected": True}, latency
                )
        except socket.timeout:
            latency = int((ctx.clock() - started) * 1000)
            return Observation(
                {"host": host, "port": port, "connected": False, "reason": "连接超时"},
                latency,
            )
        except OSError as exc:
            latency = int((ctx.clock() - started) * 1000)
            return Observation(
                {
                    "host": host,
                    "port": port,
                    "connected": False,
                    "reason": truncate(str(exc), 200),
                },
                latency,
            )


PROBE = TcpProbe()
