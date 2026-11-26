"""dns：经本地 DNS 解析域名（busybox nslookup）。"""

import re

from ..base import Observation, Probe, observe_error, truncate

_IPV4_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
_IPV6_RE = re.compile(r"\b([0-9a-fA-F]{0,4}(?::[0-9a-fA-F]{0,4}){2,7})\b")


def _extract_addresses(output):
    """从 nslookup 输出中提取应答地址，跳过 DNS 服务端自身地址。"""
    if not output:
        return []
    index = output.find("Name:")
    segment = output[index:] if index >= 0 else output
    addresses = []
    for candidate in _IPV4_RE.findall(segment):
        if not candidate.startswith("127.") and candidate not in addresses:
            addresses.append(candidate)
    for candidate in _IPV6_RE.findall(segment):
        if candidate not in (":", "::") and not candidate.startswith("::1"):
            if candidate not in addresses:
                addresses.append(candidate)
    return addresses


class DnsProbe(Probe):
    id = "dns"
    name = "DNS 解析"
    description = "经本地 DNS 解析指定域名"
    params_schema = {
        "name": {
            "type": "string",
            "required": True,
            "label": "域名",
            "placeholder": "www.baidu.com",
        },
        "server": {
            "type": "string",
            "required": False,
            "default": "127.0.0.1",
            "label": "DNS 服务器",
        },
    }
    default_expect = {"kind": "dns", "resolved": True}
    default_timeout = 3.0
    remote_allowed = True

    def observe(self, ctx, params):
        domain = str(params.get("name") or "www.baidu.com")
        server = str(params.get("server") or "127.0.0.1")
        started = ctx.clock()
        result = ctx.run(["nslookup", domain, server])
        latency = int((ctx.clock() - started) * 1000)
        base = {"domain": domain, "server": server, "timeout_seconds": ctx.timeout}

        if result.timed_out:
            return observe_error("nslookup 超时", latency, base)
        if result.error:
            return observe_error(truncate(result.error), latency, base)
        if result.exit_code != 0:
            return Observation(
                {
                    **base,
                    "resolved": False,
                    "addresses": [],
                    "exit_code": result.exit_code,
                    "stderr": truncate(result.stderr),
                },
                latency,
            )

        addresses = _extract_addresses(result.stdout)
        return Observation(
            {
                **base,
                "resolved": bool(addresses),
                "addresses": addresses,
                "address": addresses[0] if addresses else "",
            },
            latency,
        )


PROBE = DnsProbe()
