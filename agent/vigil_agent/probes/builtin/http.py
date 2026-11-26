"""http：HTTP 请求观测（显式忽略系统代理）。"""

import time
import urllib.error
import urllib.request

from ... import __version__
from ..base import Observation, Probe, observe_error, truncate

MAX_BODY = 8192

# 不使用环境变量代理，避免 OpenClash 等代理故障影响判断
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def urllib_get(url, timeout, method="GET", headers=None):
    """默认实现：返回 (status, body, latency_ms, error)。"""
    request_headers = {
        "User-Agent": "vigil-agent/%s" % __version__,
        "Cache-Control": "no-cache",
        "Accept": "*/*",
    }
    for key, value in (headers or {}).items():
        request_headers[str(key)] = str(value)

    started = time.monotonic()
    request = urllib.request.Request(url, headers=request_headers, method=method or "GET")
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            raw = response.read(MAX_BODY)
            latency = int((time.monotonic() - started) * 1000)
            status = getattr(response, "status", None) or response.getcode()
            return int(status), raw.decode("utf-8", "replace"), latency, ""
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read(MAX_BODY)
        except Exception:
            raw = b""
        latency = int((time.monotonic() - started) * 1000)
        return int(exc.code), raw.decode("utf-8", "replace"), latency, ""
    except Exception as exc:  # 网络错误、超时、TLS 失败统一按不可达处理
        latency = int((time.monotonic() - started) * 1000)
        return 0, "", latency, truncate(str(exc), 200)


class HttpProbe(Probe):
    id = "http"
    name = "HTTP"
    description = "请求指定 URL，按状态码 / 正文 / JSON / 时延判定"
    params_schema = {
        "url": {
            "type": "string",
            "required": True,
            "label": "URL",
            "placeholder": "http://connect.rom.miui.com/generate_204",
        },
        "method": {
            "type": "enum",
            "values": ["GET", "HEAD", "POST"],
            "required": False,
            "default": "GET",
            "label": "方法",
        },
        "headers": {"type": "object", "required": False, "label": "请求头"},
    }
    default_expect = {"kind": "http", "status": [200, 204]}
    default_timeout = 5.0
    remote_allowed = True

    def observe(self, ctx, params):
        url = str(params.get("url") or "").strip()
        if not url:
            return observe_error("缺少 url 参数")
        method = str(params.get("method") or "GET").upper()
        headers = params.get("headers") if isinstance(params.get("headers"), dict) else {}
        probe_func = ctx.http_get or urllib_get

        started = ctx.clock()
        status, body, latency, error = probe_func(url, ctx.timeout, method, headers)
        if latency is None:
            latency = int((ctx.clock() - started) * 1000)

        data = {
            "url": url,
            "method": method,
            "status": int(status or 0),
            "body": truncate(body or "", MAX_BODY),
        }
        return Observation(data, int(latency or 0), error)


PROBE = HttpProbe()
