"""本地 HTTP 请求处理：路由分发、会话鉴权、响应封装与静态资源。"""

import hmac
import json
import logging
import mimetypes
import os
import re
import secrets
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler

from .static import PLACEHOLDER_HTML, resolve_static_path

LOGGER = logging.getLogger("vigil.httpd")

SESSION_COOKIE = "vigil_session"
MAX_BODY_BYTES = 64 * 1024
DEFAULT_EVENT_LIMIT = 50
MAX_LIMIT = 500

ACTION_PATH_RE = re.compile(r"^/api/actions/([A-Za-z0-9_]+)$")
PROBE_DELETE_RE = re.compile(r"^/api/probes/([a-z0-9][a-z0-9._-]{0,63})$")
PROBE_TEST_RE = re.compile(
    r"^/api/probes/(local\.[a-z0-9][a-z0-9._-]{0,48})/test$"
)
CHECK_TEST_RE = re.compile(r"^/api/checks/([a-z0-9][a-z0-9._-]{0,63})/test$")
MAX_PROBE_PACKAGE_BYTES = 8 * 1024 * 1024


class VigilRequestHandler(BaseHTTPRequestHandler):
    server_version = "vigil"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # ---------- HTTP 入口 ----------

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path.startswith("/api/"):
            query = urllib.parse.parse_qs(parsed.query)
            self._handle_api("GET", parsed.path, query)
            return
        self._serve_static(parsed.path)

    def do_POST(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path.startswith("/api/"):
            query = urllib.parse.parse_qs(parsed.query)
            self._handle_api("POST", parsed.path, query)
            return
        self._send_json(404, {"error": "not_found"})

    def do_PUT(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path.startswith("/api/"):
            query = urllib.parse.parse_qs(parsed.query)
            self._handle_api("PUT", parsed.path, query)
            return
        self._send_json(404, {"error": "not_found"})

    def do_DELETE(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path.startswith("/api/"):
            query = urllib.parse.parse_qs(parsed.query)
            self._handle_api("DELETE", parsed.path, query)
            return
        self._send_json(404, {"error": "not_found"})

    # ---------- API ----------

    def _handle_api(self, method, path, query):
        if method == "POST" and path == "/api/login":
            self._handle_login()
            return

        if not self._authenticated():
            self._send_json(401, {"error": "unauthorized"})
            return

        if method == "GET" and path == "/api/status":
            self._serve_provider(self.server.status_provider)
            return
        if method == "GET" and path == "/api/checks":
            self._serve_provider(self.server.checks_provider)
            return
        if method == "GET" and path == "/api/probes":
            self._serve_provider(self.server.probes_provider)
            return
        if method == "DELETE":
            match = PROBE_DELETE_RE.match(path)
            if match:
                self._handle_probe_delete(match.group(1))
                return
        if method == "POST":
            if path == "/api/probes":
                self._handle_probe_install()
                return
            match = PROBE_TEST_RE.match(path)
            if match:
                self._handle_probe_test(match.group(1))
                return
            match = CHECK_TEST_RE.match(path)
            if match:
                self._handle_check_test(match.group(1))
                return
            match = ACTION_PATH_RE.match(path)
            if match:
                self._handle_action(match.group(1))
                return
        if method == "GET" and path == "/api/events":
            limit = _parse_limit(query, DEFAULT_EVENT_LIMIT)
            self._serve_provider(self.server.events_provider, limit)
            return
        if method == "GET" and path == "/api/logs":
            self._serve_provider(self.server.logs_provider, **_parse_log_filters(query))
            return
        if method == "GET" and path == "/api/logs/export":
            self._handle_logs_export(query)
            return
        if method == "PUT" and path == "/api/checks":
            self._handle_checks_update()
            return
        if method == "GET" and path == "/api/guards":
            self._serve_provider(self.server.guards_provider)
            return
        if method == "PUT" and path == "/api/guards":
            self._handle_guards_update()
            return
        if method == "GET" and path == "/api/settings":
            self._serve_provider(self.server.settings_provider)
            return
        if method == "PUT" and path == "/api/settings":
            self._handle_settings_update()
            return
        if method == "POST" and path == "/api/pairing":
            self._handle_pairing_start()
            return
        if method == "POST" and path == "/api/pairing/poll":
            self._handle_pairing(self.server.pairing_poll_handler)
            return
        if method == "DELETE" and path == "/api/pairing":
            self._handle_pairing(self.server.pairing_cancel_handler)
            return

        self._send_json(404, {"error": "not_found"})

    def _serve_provider(self, provider, *args, **kwargs):
        if provider is None:
            self._send_json(200, [] if args or kwargs else {})
            return
        try:
            payload = provider(*args, **kwargs)
        except Exception:
            LOGGER.exception("本地 API 数据提供方异常")
            self._send_json(500, {"error": "internal_error"})
            return
        self._send_json(200, payload)

    def _handle_checks_update(self):
        """PUT /api/checks：整表替换本地检测项配置。"""
        body, error = self._read_json()
        if error:
            self._send_json(400, {"error": error})
            return
        checks = body.get("checks")
        if not isinstance(checks, list):
            self._send_json(400, {"error": "checks 必须是数组"})
            return

        handler = self.server.checks_handler
        if handler is None:
            self._send_json(500, {"error": "检测项编辑未启用"})
            return
        try:
            payload = handler(checks)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc) or "检测项配置非法"})
            return
        except Exception:
            LOGGER.exception("更新本地检测项失败")
            self._send_json(500, {"error": "internal_error"})
            return
        self._send_json(200, payload)

    def _handle_guards_update(self):
        """PUT /api/guards：整表替换本地守护项配置。"""
        body, error = self._read_json()
        if error:
            self._send_json(400, {"error": error})
            return
        guards = body.get("guards")
        if not isinstance(guards, list):
            self._send_json(400, {"error": "guards 必须是数组"})
            return

        handler = self.server.guards_handler
        if handler is None:
            self._send_json(500, {"error": "守护项编辑未启用"})
            return
        try:
            payload = handler(guards)
        except ValueError as exc:
            if str(exc) == "managed_by_server":
                self._send_json(
                    409,
                    {
                        "error": "managed_by_server",
                        "reason": "守护项由服务端下发，本地不可编辑",
                    },
                )
                return
            self._send_json(400, {"error": str(exc) or "守护项配置非法"})
            return
        except Exception:
            LOGGER.exception("更新本地守护项失败")
            self._send_json(500, {"error": "internal_error"})
            return
        self._send_json(200, payload)

    def _handle_logs_export(self, query):
        """GET /api/logs/export：按过滤条件下载 JSONL 日志。"""
        provider = self.server.logs_export_provider
        if provider is None:
            self._send_json(500, {"error": "日志导出未启用"})
            return
        filters = _parse_log_filters(query)
        filters.pop("limit", None)
        try:
            data = provider(**filters)
        except Exception:
            LOGGER.exception("导出日志失败")
            self._send_json(500, {"error": "internal_error"})
            return
        filename = "vigil-logs-%s.jsonl" % time.strftime("%Y%m%d-%H%M%S")
        self._send_bytes(
            200,
            data if isinstance(data, bytes) else str(data).encode("utf-8"),
            "application/x-ndjson; charset=utf-8",
            {"Content-Disposition": 'attachment; filename="%s"' % filename},
        )

    def _handle_settings_update(self):
        """PUT /api/settings：更新客户端运行设置。"""
        handler = self.server.settings_handler
        if handler is None:
            self._send_json(500, {"error": "设置编辑未启用"})
            return
        body, error = self._read_json()
        if error:
            self._send_json(400, {"error": error})
            return
        if not isinstance(body, dict):
            self._send_json(400, {"error": "请求体必须是对象"})
            return
        try:
            payload = handler(body)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc) or "设置非法"})
            return
        except Exception:
            LOGGER.exception("更新客户端设置失败")
            self._send_json(500, {"error": "internal_error"})
            return
        self._send_json(200, payload)

    def _handle_pairing_start(self):
        """POST /api/pairing：保存服务端地址并发起配对。"""
        handler = self.server.pairing_start_handler
        if handler is None:
            self._send_json(500, {"error": "配对未启用"})
            return
        body, error = self._read_json()
        if error:
            self._send_json(400, {"error": error})
            return
        base_url = body.get("base_url")
        if not isinstance(base_url, str):
            self._send_json(400, {"error": "base_url 必须是字符串"})
            return
        try:
            payload = handler(base_url)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc) or "配对失败"})
            return
        except Exception:
            LOGGER.exception("发起配对失败")
            self._send_json(500, {"error": "internal_error"})
            return
        self._send_json(200, payload)

    def _handle_pairing(self, handler):
        """POST /api/pairing/poll 与 DELETE /api/pairing。"""
        if handler is None:
            self._send_json(500, {"error": "配对未启用"})
            return
        try:
            payload = handler()
        except ValueError as exc:
            self._send_json(400, {"error": str(exc) or "配对操作失败"})
            return
        except Exception:
            LOGGER.exception("配对操作失败")
            self._send_json(500, {"error": "internal_error"})
            return
        self._send_json(200, payload)

    def _handle_login(self):
        if not self.server.token:
            self._send_json(401, {"error": "token_not_configured"})
            return

        body, error = self._read_json()
        if error:
            self._send_json(400, {"error": error})
            return
        token = body.get("token")
        if not isinstance(token, str):
            self._send_json(400, {"error": "token 必须是字符串"})
            return

        now = time.time()
        ip = self.client_address[0] if self.client_address else "unknown"
        with self.server.lock:
            failures = [
                stamp
                for stamp in self.server.login_failures.get(ip, [])
                if now - stamp < self.server.login_window
            ]
            if len(failures) >= self.server.login_max_failures:
                self.server.login_failures[ip] = failures
                retry_after = max(
                    1,
                    int(self.server.login_window - (now - failures[0])),
                )
                self._send_json(
                    429,
                    {"error": "too_many_attempts"},
                    {"Retry-After": str(retry_after)},
                )
                return

            if not hmac.compare_digest(
                token.encode("utf-8"), self.server.token.encode("utf-8")
            ):
                failures.append(now)
                self.server.login_failures[ip] = failures
                remaining = self.server.login_max_failures - len(failures)
                self._send_json(
                    401,
                    {"error": "invalid_token", "remaining_attempts": max(0, remaining)},
                )
                return

            session = secrets.token_urlsafe(32)
            self.server.sessions[session] = now + self.server.session_ttl
            self.server.login_failures.pop(ip, None)

        cookie = "%s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d" % (
            SESSION_COOKIE,
            session,
            self.server.session_ttl,
        )
        self._send_json(
            200,
            {"ok": True, "expires_in": self.server.session_ttl},
            {"Set-Cookie": cookie},
        )

    def _handle_probe_install(self):
        """POST /api/probes：上传 tar.gz 安装本地 probe 包。"""
        handler = self.server.probes_install_handler
        if handler is None:
            self._send_json(500, {"error": "probe 安装未启用"})
            return
        length = self._content_length()
        if length <= 0:
            self._send_json(400, {"error": "请求体为空"})
            return
        if length > MAX_PROBE_PACKAGE_BYTES:
            self._send_json(413, {"error": "probe 包超过允许大小"})
            return
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type not in (
            "application/gzip",
            "application/x-gzip",
            "application/octet-stream",
            "",
        ):
            self._send_json(400, {"error": "probe 包必须使用 application/gzip"})
            return
        data = self.rfile.read(length)
        if data[:2] != b"\x1f\x8b":
            self._send_json(400, {"error": "上传内容不是 gzip 压缩包"})
            return
        try:
            payload = handler(data)
        except LookupError as exc:
            self._send_json(404, {"error": str(exc) or "not_found"})
            return
        except ValueError as exc:
            self._send_json(400, {"error": str(exc) or "probe 包非法"})
            return
        except Exception:
            LOGGER.exception("安装本地 probe 包失败")
            self._send_json(500, {"error": "internal_error"})
            return
        self._send_json(200, payload)

    def _handle_probe_delete(self, probe_id):
        """DELETE /api/probes/{id}：删除本地 probe 包。"""
        handler = self.server.probes_delete_handler
        if handler is None:
            self._send_json(500, {"error": "probe 删除未启用"})
            return
        try:
            payload = handler(probe_id)
        except LookupError as exc:
            self._send_json(404, {"error": str(exc) or "not_found"})
            return
        except ValueError as exc:
            self._send_json(400, {"error": str(exc) or "probe id 非法"})
            return
        except Exception:
            LOGGER.exception("删除本地 probe 包失败")
            self._send_json(500, {"error": "internal_error"})
            return
        self._send_json(200, payload)

    def _handle_probe_test(self, probe_id):
        """POST /api/probes/{id}/test：试运行探测器。"""
        handler = self.server.probes_test_handler
        if handler is None:
            self._send_json(500, {"error": "probe 试运行未启用"})
            return
        body = {}
        if self._content_length():
            body, error = self._read_json()
            if error:
                self._send_json(400, {"error": error})
                return
        params = body.get("params")
        if params is not None and not isinstance(params, dict):
            self._send_json(400, {"error": "params 必须是对象"})
            return
        expect = body.get("expect")
        if expect is not None and not isinstance(expect, dict):
            self._send_json(400, {"error": "expect 必须是对象"})
            return
        try:
            payload = handler(probe_id, params, expect)
        except LookupError as exc:
            self._send_json(404, {"error": str(exc) or "not_found"})
            return
        except ValueError as exc:
            self._send_json(400, {"error": str(exc) or "参数非法"})
            return
        except Exception:
            LOGGER.exception("试运行本地 probe 失败")
            self._send_json(500, {"error": "internal_error"})
            return
        self._send_json(200, payload)

    def _handle_check_test(self, check_id):
        """POST /api/checks/{id}/test：按定义试运行单个检测项。"""
        handler = self.server.checks_test_handler
        if handler is None:
            self._send_json(500, {"error": "检测项试运行未启用"})
            return
        if self._content_length():
            _body, error = self._read_json()
            if error:
                self._send_json(400, {"error": error})
                return
        try:
            payload = handler(check_id)
        except LookupError as exc:
            self._send_json(404, {"error": str(exc) or "not_found"})
            return
        except ValueError as exc:
            self._send_json(400, {"error": str(exc) or "检测项非法"})
            return
        except Exception:
            LOGGER.exception("试运行检测项失败")
            self._send_json(500, {"error": "internal_error"})
            return
        self._send_json(200, payload)

    def _handle_action(self, name):
        body = {}
        length = self._content_length()
        if length:
            body, error = self._read_json()
            if error:
                self._send_json(400, {"error": error})
                return
        target = body.get("target")
        if target is not None and not isinstance(target, str):
            self._send_json(400, {"error": "target 必须是字符串"})
            return

        handler = self.server.action_handler
        if handler is None:
            self._send_json(403, {"error": "action_not_allowed", "reason": "not_whitelisted"})
            return
        try:
            result = handler(name, target)
        except Exception:
            LOGGER.exception("执行本地动作失败")
            self._send_json(500, {"error": "internal_error"})
            return

        if not isinstance(result, dict):
            self._send_json(500, {"error": "internal_error"})
            return
        if not result.get("allowed"):
            reason = result.get("reason") or "not_whitelisted"
            status = 403 if reason in (
                "not_whitelisted",
                "target_not_whitelisted",
                "target_invalid",
                "target_required",
                "level_disabled",
            ) else 409
            self._send_json(status, result)
            return
        self._send_json(200, result)

    def _authenticated(self):
        token = self._cookie_value(SESSION_COOKIE)
        if not token:
            return False
        now = time.time()
        with self.server.lock:
            for key, expiry in list(self.server.sessions.items()):
                if expiry <= now:
                    self.server.sessions.pop(key, None)
            expiry = self.server.sessions.get(token)
            return bool(expiry and expiry > now)

    def _cookie_value(self, name):
        raw = self.headers.get("Cookie", "")
        for item in raw.split(";"):
            key, _, value = item.strip().partition("=")
            if key == name:
                return value
        return ""

    # ---------- 静态资源 ----------

    def _serve_static(self, path):
        root = self.server.www_root
        if not root or not os.path.isdir(root):
            self._send_html(200, PLACEHOLDER_HTML)
            return

        full_path, error = resolve_static_path(root, path)
        if error == "forbidden":
            self._send_json(403, {"error": "forbidden"})
            return
        if full_path is None:
            self._send_json(404, {"error": "not_found"})
            return

        content_type = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
        try:
            with open(full_path, "rb") as handle:
                data = handle.read()
        except OSError:
            self._send_json(404, {"error": "not_found"})
            return
        self._send_bytes(200, data, content_type)

    # ---------- 响应与请求工具 ----------

    def _content_length(self):
        raw = self.headers.get("Content-Length")
        if not raw:
            return 0
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 0

    def _read_json(self):
        length = self._content_length()
        if length > MAX_BODY_BYTES:
            return None, "请求体过大"
        if not length:
            return {}, ""
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None, "请求体不是合法 JSON"
        if not isinstance(data, dict):
            return None, "请求体必须是 JSON 对象"
        return data, ""

    def _send_json(self, status, payload, extra_headers=None):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, data, "application/json; charset=utf-8", extra_headers)

    def _send_html(self, status, html):
        self._send_bytes(status, html.encode("utf-8"), "text/html; charset=utf-8")

    def _send_bytes(self, status, data, content_type, extra_headers=None):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt, *args):
        LOGGER.debug("%s %s", self.address_string(), fmt % args)


def _parse_limit(query, default):
    values = query.get("limit") if query else None
    if not values:
        return default
    try:
        value = int(values[0])
    except (TypeError, ValueError):
        return default
    return max(1, min(MAX_LIMIT, value))


def _parse_log_filters(query):
    def first(name):
        values = query.get(name) if query else None
        if not values:
            return None
        text = str(values[0]).strip()
        return text or None

    since = None
    raw_since = first("since")
    if raw_since:
        try:
            since = float(raw_since)
        except ValueError:
            since = None
    return {
        "limit": _parse_limit(query, 200),
        "level": first("level"),
        "event": first("event"),
        "guard_id": first("guard_id"),
        "recovery_id": first("recovery_id"),
        "since": since,
    }
