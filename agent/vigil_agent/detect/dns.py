"""net.dns：通过本机 127.0.0.1 解析固定域名。

使用系统自带的 nslookup（busybox 实现），超时 3s，避免依赖额外工具。
"""

import re

from .base import (
    STATUS_CRITICAL,
    STATUS_OK,
    STATUS_UNKNOWN,
    STATUS_WARN,
    make_result,
    truncate,
)

LAYER = "net.dns"
DEPENDS_ON = ("net.route",)

_IPV4_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
_IPV6_RE = re.compile(r"\b([0-9a-fA-F]{0,4}(?::[0-9a-fA-F]{0,4}){2,7})\b")


def _extract_address(output):
    """从 nslookup 输出中提取应答地址，跳过 127.0.0.1 这类服务端地址。"""
    if not output:
        return ""
    index = output.find("Name:")
    segment = output[index:] if index >= 0 else output
    for candidate in _IPV4_RE.findall(segment):
        if not candidate.startswith("127."):
            return candidate
    for candidate in _IPV6_RE.findall(segment):
        if candidate not in (":", "::") and not candidate.startswith("::1"):
            return candidate
    return ""


def detect(ctx):
    domain = str(ctx.detect.get("dns_probe") or "www.baidu.com")
    timeout = ctx.timeout
    started = ctx.clock()
    result = ctx.runner.run(["nslookup", domain, "127.0.0.1"], timeout=timeout)
    latency_ms = int((ctx.clock() - started) * 1000)

    base_evidence = {
        "domain": domain,
        "server": "127.0.0.1",
        "timeout_seconds": timeout,
    }

    if result.timed_out:
        base_evidence["error"] = "nslookup 超时"
        return make_result(LAYER, STATUS_CRITICAL, latency_ms, base_evidence)

    if result.error:
        base_evidence["error"] = truncate(result.error, 512)
        return make_result(LAYER, STATUS_UNKNOWN, latency_ms, base_evidence)

    if result.exit_code != 0:
        base_evidence["exit_code"] = result.exit_code
        base_evidence["stderr"] = truncate(result.stderr, 512)
        return make_result(LAYER, STATUS_CRITICAL, latency_ms, base_evidence)

    address = _extract_address(result.stdout)
    if not address:
        base_evidence["reason"] = "nslookup 退出码为 0 但未解析到地址"
        base_evidence["output"] = truncate(result.stdout, 512)
        return make_result(LAYER, STATUS_WARN, latency_ms, base_evidence)

    base_evidence["address"] = address
    return make_result(LAYER, STATUS_OK, latency_ms, base_evidence)
