"""net.http：直连 204 端点探测。

- 每个目标 3s 超时，任一返回 204 即视为链路可用
- 显式忽略环境变量代理（OpenClash 等代理挂掉时不应影响判断）
- 返回 2xx 但不是 204 时降级为 warn
"""

import time
import urllib.error
import urllib.request

from .. import __version__
from .base import (
    STATUS_CRITICAL,
    STATUS_OK,
    STATUS_UNKNOWN,
    STATUS_WARN,
    make_result,
    truncate,
)

LAYER = "net.http"
DEPENDS_ON = ("net.dns",)

# 构建一个不走环境变量代理的 opener；模块级复用
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def urllib_probe(url, timeout):
    """默认探测实现：返回 (status_code, latency_ms, error)。

    status_code 为 0 表示网络层失败。
    """
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "vigil-agent/%s" % __version__,
            "Cache-Control": "no-cache",
            "Accept": "*/*",
        },
    )
    started = time.monotonic()
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            latency = int((time.monotonic() - started) * 1000)
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            return int(status), latency, ""
    except urllib.error.HTTPError as exc:
        latency = int((time.monotonic() - started) * 1000)
        return int(exc.code), latency, truncate(str(exc.reason or exc), 200)
    except Exception as exc:  # 网络错误、超时、TLS 失败统一按不可达处理
        latency = int((time.monotonic() - started) * 1000)
        return 0, latency, truncate(str(exc), 200)


def detect(ctx):
    probes = ctx.detect.get("http_probes") or []
    timeout = ctx.timeout

    if not probes:
        return make_result(
            LAYER,
            STATUS_UNKNOWN,
            0,
            {"reason": "未配置 http_probes"},
        )

    probe_func = ctx.http_get or urllib_probe
    attempts = []
    warn_latency = None
    warn_url = ""
    total_started = ctx.clock()

    for url in probes:
        status_code, latency, error = probe_func(url, timeout)
        attempts.append(
            {
                "url": url,
                "status": status_code,
                "latency_ms": int(latency),
                "error": truncate(error, 200) if error else "",
            }
        )
        if status_code == 204:
            return make_result(
                LAYER,
                STATUS_OK,
                int(latency),
                {"url": url, "status": 204, "attempts": attempts},
            )
        if 200 <= int(status_code) < 300 and warn_latency is None:
            warn_latency = int(latency)
            warn_url = url

    total_latency = int((ctx.clock() - total_started) * 1000)
    if warn_latency is not None:
        return make_result(
            LAYER,
            STATUS_WARN,
            warn_latency,
            {
                "reason": "端点返回 2xx 但不是 204",
                "url": warn_url,
                "attempts": attempts,
            },
        )

    return make_result(
        LAYER,
        STATUS_CRITICAL,
        total_latency,
        {"reason": "全部 HTTP 探测目标不可达", "attempts": attempts},
    )
