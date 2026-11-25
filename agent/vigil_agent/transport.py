"""服务端通信：注册、beacon、心跳、事件补传、配置拉取。

仅使用标准库 urllib。所有请求带 Bearer 鉴权与时间戳/随机数头，
网络错误与 5xx 做有限重试，4xx 不重试。
"""

import http.client
import json
import os
import ssl
import time
import urllib.error
import urllib.request
import uuid

from . import PROTOCOL_VERSION, __version__

DEFAULT_TIMEOUT = 10
BACKOFF_SECONDS = (1.0, 3.0)


class TransportError(Exception):
    """网络层错误。"""


def read_boot_id(
    proc_path="/proc/sys/kernel/random/boot_id",
    fallback_path="/overlay/vigil/boot_id",
    generator=None,
):
    """读取内核 boot_id；不存在时使用持久化随机值，保证跨重启稳定。"""
    try:
        with open(proc_path, "r", encoding="utf-8") as handle:
            value = handle.read().strip()
        if value:
            return value
    except OSError:
        pass

    try:
        with open(fallback_path, "r", encoding="utf-8") as handle:
            value = handle.read().strip()
        if value:
            return value
    except OSError:
        pass

    value = str(generator() if generator else uuid.uuid4())
    try:
        directory = os.path.dirname(fallback_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(fallback_path, "w", encoding="utf-8") as handle:
            handle.write(value)
        try:
            os.chmod(fallback_path, 0o600)
        except OSError:
            pass
    except OSError:
        pass
    return value


class UrllibHttpClient:
    """极简 HTTP 客户端，便于在测试中替换。"""

    def __init__(self, verify_tls=True, timeout=DEFAULT_TIMEOUT):
        self.verify_tls = bool(verify_tls)
        self.timeout = timeout

    def _ssl_context(self):
        if self.verify_tls:
            return ssl.create_default_context()
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    def request(self, method, url, body=None, headers=None, timeout=None):
        """返回 (status, headers_dict, raw_bytes)。网络错误抛 TransportError。"""
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        if data is not None:
            request.add_header("Content-Type", "application/json; charset=utf-8")

        context = self._ssl_context() if url.lower().startswith("https") else None
        try:
            with urllib.request.urlopen(
                request, timeout=timeout or self.timeout, context=context
            ) as response:
                return int(response.status), dict(response.headers.items()), response.read()
        except urllib.error.HTTPError as exc:
            resp_headers = dict(exc.headers.items()) if exc.headers else {}
            try:
                raw = exc.read()
            except Exception:
                raw = b""
            return int(exc.code), resp_headers, raw
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            raise TransportError(str(exc))


def _parse_json(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}


def _header(headers, name):
    if not headers:
        return ""
    lowered = name.lower()
    for key, value in headers.items():
        if str(key).lower() == lowered:
            return str(value)
    return ""


def _error_result(message):
    return {"ok": False, "status": 0, "headers": {}, "body": {}, "error": message}


def _extract_error(body, status):
    if isinstance(body, dict):
        for key in ("error", "detail", "message"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return "%s（HTTP %s）" % (value, status)
    return "HTTP %s" % status


class Transport:
    """负责与服务端交互的客户端。"""

    def __init__(
        self,
        config,
        client=None,
        now=None,
        sleep=None,
        boot_id=None,
        save_config=None,
        timeout=DEFAULT_TIMEOUT,
    ):
        self.config = config
        server = config.get("server") or {}
        self.base_url = str(server.get("base_url") or "").rstrip("/")
        self.timeout = timeout
        self.client = client or UrllibHttpClient(
            verify_tls=server.get("verify_tls", True), timeout=timeout
        )
        self.now = now or time.time
        self._sleep = sleep or time.sleep
        self.save_config = save_config
        self.boot_id = boot_id or read_boot_id()

    # ---------- 基础 ----------

    @property
    def client_id(self):
        return str((self.config.get("server") or {}).get("client_id") or "")

    @property
    def secret(self):
        return str((self.config.get("server") or {}).get("secret") or "")

    @property
    def is_registered(self):
        return bool(self.client_id and self.secret)

    def update_credentials(self, client_id, secret):
        server = self.config.setdefault("server", {})
        server["client_id"] = client_id
        server["secret"] = secret

    def _headers(self, extra=None):
        headers = {
            "Accept": "application/json",
            "User-Agent": "vigil-agent/%s" % __version__,
        }
        if self.client_id:
            headers["X-Vigil-Client"] = self.client_id
        if self.secret:
            headers["Authorization"] = "Bearer %s" % self.secret
        headers["X-Vigil-Timestamp"] = str(int(self.now()))
        headers["X-Vigil-Nonce"] = uuid.uuid4().hex
        if extra:
            headers.update(extra)
        return headers

    def _backoff(self, attempt):
        if attempt < len(BACKOFF_SECONDS):
            return BACKOFF_SECONDS[attempt]
        return BACKOFF_SECONDS[-1]

    def _request(self, method, path, body=None, extra_headers=None, timeout=None, retries=2):
        if not self.base_url:
            return _error_result("未配置服务器地址")
        url = self.base_url + path
        last_error = ""
        for attempt in range(retries + 1):
            try:
                status, resp_headers, raw = self.client.request(
                    method,
                    url,
                    body=body,
                    headers=self._headers(extra_headers),
                    timeout=timeout or self.timeout,
                )
            except Exception as exc:  # 网络错误统一重试
                last_error = str(exc) or type(exc).__name__
                if attempt < retries:
                    self._sleep(self._backoff(attempt))
                    continue
                return _error_result(last_error)

            parsed = _parse_json(raw)
            if status >= 500 and attempt < retries:
                last_error = "HTTP %s" % status
                self._sleep(self._backoff(attempt))
                continue

            ok = 200 <= status < 300
            return {
                "ok": ok,
                "status": status,
                "headers": resp_headers or {},
                "body": parsed,
                "error": "" if ok else _extract_error(parsed, status),
            }
        return _error_result(last_error or "请求失败")

    def _require_registered(self):
        if not self.is_registered:
            return _error_result("客户端尚未注册")
        return None

    def _persist(self):
        if not self.save_config:
            return True
        try:
            self.save_config(self.config)
            return True
        except Exception:
            return False

    # ---------- 协议接口 ----------

    def register(self, name, model="", fw_version="", bootstrap_token=None):
        """使用 bootstrap token 换取 client_id / secret。"""
        server = self.config.get("server") or {}
        token = bootstrap_token if bootstrap_token is not None else server.get("bootstrap_token", "")
        body = {
            "name": name,
            "model": model,
            "fw_version": fw_version,
            "agent_version": __version__,
            "protocol": PROTOCOL_VERSION,
        }
        extra = {"X-Vigil-Bootstrap": token} if token else {}
        result = self._request("POST", "/api/v1/register", body=body, extra_headers=extra)
        if not result["ok"]:
            return result

        data = result["body"]
        client_id = data.get("client_id")
        secret = data.get("secret")
        if not isinstance(client_id, str) or not client_id:
            result["ok"] = False
            result["error"] = "注册响应缺少 client_id"
            return result
        if not isinstance(secret, str) or not secret:
            result["ok"] = False
            result["error"] = "注册响应缺少 secret"
            return result

        self.update_credentials(client_id, secret)
        heartbeat_interval = data.get("heartbeat_interval")
        if (
            isinstance(heartbeat_interval, int)
            and not isinstance(heartbeat_interval, bool)
            and 5 <= heartbeat_interval <= 3600
        ):
            self.config["server"]["heartbeat_interval"] = heartbeat_interval
        result["saved"] = self._persist()
        return result

    def beacon(self, seq, uptime, boot_id=None):
        error = self._require_registered()
        if error:
            return error
        body = {
            "client_id": self.client_id,
            "seq": int(seq),
            "boot_id": boot_id or self.boot_id,
            "uptime": int(uptime),
        }
        return self._request("POST", "/api/v1/beacon", body=body)

    def heartbeat(self, seq, uptime, modules=None, spool=None, wan_ip="", boot_id=None):
        error = self._require_registered()
        if error:
            return error
        body = {
            "client_id": self.client_id,
            "seq": int(seq),
            "boot_id": boot_id or self.boot_id,
            "uptime": int(uptime),
            "agent_version": __version__,
            "wan_ip": wan_ip or "",
            "modules": modules or {},
            "spool": spool or {"pending": 0, "dropped": 0},
        }
        return self._request("POST", "/api/v1/heartbeat", body=body)

    def events(self, events):
        error = self._require_registered()
        if error:
            return error
        normalized = []
        for event in events or []:
            if not isinstance(event, dict):
                continue
            normalized.append(
                {
                    "event_id": event.get("event_id") or uuid.uuid4().hex,
                    "seq": int(event.get("seq") or 0),
                    "type": str(event.get("type") or ""),
                    "domain": str(event.get("domain") or ""),
                    "payload": event.get("payload") or {},
                }
            )
        if not normalized:
            return {"ok": True, "status": 200, "headers": {}, "body": {}, "error": "", "sent": 0}
        body = {"client_id": self.client_id, "events": normalized}
        result = self._request("POST", "/api/v1/events", body=body)
        result["sent"] = len(normalized) if result["ok"] else 0
        return result

    def fetch_config(self, etag=None):
        extra = {"If-None-Match": etag} if etag else {}
        result = self._request("GET", "/api/v1/config", extra_headers=extra)
        status = result["status"]
        response_etag = _header(result["headers"], "ETag")
        return {
            "ok": result["ok"] or status == 304,
            "status": status,
            "etag": response_etag,
            "body": None if status == 304 else result["body"],
            "headers": result["headers"],
            "error": result["error"],
        }
