"""icmp：ping 目标地址观测。"""

import re

from ..base import Observation, Probe, observe_error, truncate

_LOSS_RE = re.compile(r"(\d+(?:\.\d+)?)%\s*packet\s*loss")
_RTT_RE = re.compile(r"min/avg/max\s*=\s*[\d.]+/([\d.]+)/[\d.]+")


class IcmpProbe(Probe):
    id = "icmp"
    name = "ICMP"
    description = "ping 目标地址，观测可达性与丢包/时延"
    params_schema = {
        "host": {"type": "string", "required": True, "label": "目标主机"},
        "count": {
            "type": "integer",
            "required": False,
            "default": 1,
            "label": "次数",
        },
    }
    default_expect = {"kind": "icmp", "reachable": True}
    default_timeout = 3.0
    remote_allowed = True

    def observe(self, ctx, params):
        host = str(params.get("host") or "").strip()
        if not host:
            return observe_error("缺少 host 参数")
        try:
            count = max(1, min(10, int(params.get("count") or 1)))
        except (TypeError, ValueError):
            count = 1

        started = ctx.clock()
        result = ctx.run(
            ["ping", "-c", str(count), "-W", str(max(1, int(ctx.timeout))), host]
        )
        latency = int((ctx.clock() - started) * 1000)
        if result.timed_out:
            return observe_error("ping 超时", latency, {"host": host})
        if result.error:
            return observe_error(truncate(result.error, 200), latency, {"host": host})

        output = result.stdout + result.stderr
        loss_match = _LOSS_RE.search(output)
        loss = float(loss_match.group(1)) if loss_match else None
        rtt_match = _RTT_RE.search(output)
        rtt = float(rtt_match.group(1)) if rtt_match else None
        reachable = result.exit_code == 0 and (loss is None or loss < 100)
        return Observation(
            {
                "host": host,
                "reachable": bool(reachable),
                "loss_percent": loss,
                "rtt_ms": rtt,
                "exit_code": result.exit_code,
            },
            latency,
        )


PROBE = IcmpProbe()
