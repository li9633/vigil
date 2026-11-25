"""客户端本地 HTTP 服务：管理员令牌登录、会话 Cookie、最小 API 与静态资源。

- 默认监听 0.0.0.0:8787（配置可改），生产环境应只绑定 LAN 桥接地址
- 登录失败按 IP 做内存限流
- 静态资源目录默认 /usr/share/vigil/www，不存在时返回占位页
- 静态文件路径经 realpath 校验，防目录穿越
"""

import hmac
import json
import logging
import mimetypes
import os
import re
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOGGER = logging.getLogger("vigil.httpd")

SESSION_COOKIE = "vigil_session"
MAX_BODY_BYTES = 64 * 1024
DEFAULT_EVENT_LIMIT = 50
MAX_LIMIT = 500

ACTION_PATH_RE = re.compile(r"^/api/actions/([A-Za-z0-9_]+)$")

# 提示：状态不能只靠颜色，占位页同样用文字说明
PLACEHOLDER_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vigil 客户端</title>
<style>
  body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; margin: 0;
         background: #0f172a; color: #e2e8f0; display: flex; min-height: 100vh;
         align-items: center; justify-content: center; }
  main { max-width: 480px; padding: 32px; }
  h1 { font-size: 22px; margin: 0 0 12px; }
  p { line-height: 1.7; color: #94a3b8; margin: 0 0 10px; }
  code { background: #1e293b; padding: 2px 6px; border-radius: 4px; color: #cbd5e1; }
</style>
</head>
<body>
<main>
  <h1>Vigil 客户端 UI 未安装</h1>
  <p>agent 已运行，但未找到前端静态资源目录
     <code>/usr/share/vigil/www</code>。</p>
  <p>请安装完整发行包，或把前端构建产物放到该目录后刷新页面。</p>
  <p>本地 API 仍可正常使用。</p>
</main>
</body>
</html>
"""


class VigilHTTPServer(ThreadingHTTPServer):
    """携带配置与内存会话的 HTTP 服务。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address,
        token,
        www_root,
        status_provider=None,
        checks_provider=None,
        events_provider=None,
        logs_provider=None,
        action_handler=None,
        session_ttl=43200,
        login_max_failures=5,
        login_window=60,
        guards_provider=None,
        guards_handler=None,
        modules_provider=None,
        script_import_handler=None,
        script_delete_handler=None,
        settings_provider=None,
        settings_handler=None,
        logs_export_provider=None,
    ):
        super().__init__(address, VigilRequestHandler)
        self.token = token or ""
        self.www_root = www_root
        self.status_provider = status_provider
        self.checks_provider = checks_provider
        self.events_provider = events_provider
        self.logs_provider = logs_provider
        self.action_handler = action_handler
        self.guards_provider = guards_provider
        self.guards_handler = guards_handler
        self.modules_provider = modules_provider
        self.script_import_handler = script_import_handler
        self.script_delete_handler = script_delete_handler
        self.settings_provider = settings_provider
        self.settings_handler = settings_handler
        self.logs_export_provider = logs_export_provider
        self.session_ttl = int(session_ttl)
        self.login_max_failures = int(login_max_failures)
        self.login_window = int(login_window)
        self.sessions = {}
        self.login_failures = {}
        self.lock = threading.Lock()


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
        if method == "GET" and path == "/api/guards":
            self._serve_provider(self.server.guards_provider)
            return
        if method == "PUT" and path == "/api/guards":
            self._handle_guards_update()
            return
        if method == "GET" and path == "/api/modules":
            self._serve_provider(self.server.modules_provider)
            return
        if method == "POST" and path == "/api/modules/script":
            self._handle_script_import()
            return
        if method == "POST" and path == "/api/modules/script/delete":
            self._handle_script_delete()
            return
        if method == "GET" and path == "/api/settings":
            self._serve_provider(self.server.settings_provider)
            return
        if method == "PUT" and path == "/api/settings":
            self._handle_settings_update()
            return
        if method == "POST":
            match = ACTION_PATH_RE.match(path)
            if match:
                self._handle_action(match.group(1))
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

    def _handle_script_import(self):
        """POST /api/modules/script：导入脚本探测器。"""
        handler = self.server.script_import_handler
        if handler is None:
            self._send_json(500, {"error": "脚本探测器管理未启用"})
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
            self._send_json(400, {"error": str(exc) or "脚本探测器配置非法"})
            return
        except Exception:
            LOGGER.exception("导入脚本探测器失败")
            self._send_json(500, {"error": "internal_error"})
            return
        self._send_json(200, payload)

    def _handle_script_delete(self):
        """POST /api/modules/script/delete：删除脚本探测器。"""
        handler = self.server.script_delete_handler
        if handler is None:
            self._send_json(500, {"error": "脚本探测器管理未启用"})
            return
        body, error = self._read_json()
        if error:
            self._send_json(400, {"error": error})
            return
        name = body.get("name") if isinstance(body, dict) else None
        if not isinstance(name, str) or not name.strip():
            self._send_json(400, {"error": "name 不能为空"})
            return
        try:
            payload = handler(name.strip())
        except ValueError as exc:
            self._send_json(400, {"error": str(exc) or "删除失败"})
            return
        except Exception:
            LOGGER.exception("删除脚本探测器失败")
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

        relative = urllib.parse.unquote(path.lstrip("/"))
        if not relative:
            relative = "index.html"
        elif relative.endswith("/"):
            relative += "index.html"

        root_real = os.path.realpath(root)
        full_path = os.path.realpath(os.path.join(root_real, relative))
        if full_path != root_real and not full_path.startswith(root_real + os.sep):
            self._send_json(403, {"error": "forbidden"})
            return
        if os.path.isdir(full_path):
            full_path = os.path.join(full_path, "index.html")
        if not os.path.isfile(full_path):
            # SPA 深链接（/guards、/login 等）回退到 index.html；带扩展名的资源仍 404
            index_path = os.path.join(root_real, "index.html")
            if "." not in os.path.basename(relative) and os.path.isfile(index_path):
                full_path = index_path
            else:
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
        "since": since,
    }


def create_server(
    bind,
    port,
    token,
    www_root,
    status_provider=None,
    checks_provider=None,
    events_provider=None,
    logs_provider=None,
    action_handler=None,
    session_ttl=43200,
    login_max_failures=5,
    login_window=60,
    guards_provider=None,
    guards_handler=None,
    modules_provider=None,
    script_import_handler=None,
    script_delete_handler=None,
    settings_provider=None,
    settings_handler=None,
    logs_export_provider=None,
):
    """创建（未启动的）HTTP 服务实例。"""
    return VigilHTTPServer(
        (bind, int(port)),
        token,
        www_root,
        status_provider=status_provider,
        checks_provider=checks_provider,
        events_provider=events_provider,
        logs_provider=logs_provider,
        action_handler=action_handler,
        session_ttl=session_ttl,
        login_max_failures=login_max_failures,
        login_window=login_window,
        guards_provider=guards_provider,
        guards_handler=guards_handler,
        modules_provider=modules_provider,
        script_import_handler=script_import_handler,
        script_delete_handler=script_delete_handler,
        settings_provider=settings_provider,
        settings_handler=settings_handler,
        logs_export_provider=logs_export_provider,
    )
