"""本地 HTTP 服务：鉴权、限流、路由、静态资源与防穿越测试。"""

import http.client
import json
import os
import tempfile
import threading
import time
import unittest

from vigil_agent.httpd import create_server


class HttpdTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.www = os.path.join(self.root, "www")
        os.makedirs(os.path.join(self.www, "assets"))
        with open(os.path.join(self.www, "index.html"), "w", encoding="utf-8") as handle:
            handle.write("<html><body>Vigil UI</body></html>")
        with open(
            os.path.join(self.www, "assets", "app.js"), "w", encoding="utf-8"
        ) as handle:
            handle.write("console.log('vigil');")

        self.action_calls = []
        self.event_limits = []
        self.log_limits = []
        self.server = None
        self.thread = None
        self.start_server(self.www)

    def tearDown(self):
        self.stop_server()
        self.tmp.cleanup()

    # ---------- 辅助 ----------

    def _events(self, limit):
        self.event_limits.append(limit)
        items = [{"seq": index + 1, "type": "t"} for index in range(5)]
        return items[:limit]

    def _logs(self, limit, **filters):
        self.log_limits.append(limit)
        self.log_filters = filters
        return [{"level": "INFO", "message": "hello"}][:limit]

    def _logs_export(self, **filters):
        self.export_filters = filters
        return b'{"event":"guard.state","msg":"ok"}\n'

    def _action(self, name, target):
        self.action_calls.append((name, target))
        if name in ("ifup_wan", "restart_service"):
            if name == "restart_service" and target != "dnsmasq":
                return {"allowed": False, "reason": "target_not_whitelisted"}
            return {
                "allowed": True,
                "executed": True,
                "action": name,
                "target": target or "",
                "result": "success",
            }
        return {"allowed": False, "reason": "not_whitelisted"}

    def start_server(self, www_root, token="admin-token", login_max_failures=5):
        self.server = create_server(
            "127.0.0.1",
            0,
            token,
            www_root,
            status_provider=lambda: {"agent_version": "1.0.0", "registered": True},
            checks_provider=lambda: [{"layer": "net.carrier", "status": "ok"}],
            events_provider=self._events,
            logs_provider=self._logs,
            logs_export_provider=self._logs_export,
            action_handler=self._action,
            login_max_failures=login_max_failures,
            login_window=60,
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop_server(self):
        if self.server is not None:
            try:
                self.server.shutdown()
            except Exception:
                pass
            try:
                self.server.server_close()
            except Exception:
                pass
        if self.thread is not None:
            self.thread.join(2)
        self.server = None
        self.thread = None

    def request(self, method, path, body=None, cookie="", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        request_headers = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
            request_headers["Content-Length"] = str(len(data))
        if cookie:
            request_headers["Cookie"] = cookie
        connection.request(method, path, body=data, headers=request_headers)
        response = connection.getresponse()
        raw = response.read()
        status = response.status
        set_cookie = response.getheader("Set-Cookie") or ""
        content_type = response.getheader("Content-Type") or ""
        connection.close()

        payload = None
        if raw:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except ValueError:
                payload = raw.decode("utf-8", "replace")
        return status, payload, set_cookie, content_type

    def request_raw(self, method, path, cookie=""):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Cookie": cookie} if cookie else {}
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        status = response.status
        response_headers = {key: value for key, value in response.getheaders()}
        connection.close()
        return status, raw, response_headers

    def login(self, token="admin-token"):
        status, payload, set_cookie, _ctype = self.request(
            "POST", "/api/login", {"token": token}
        )
        cookie = set_cookie.split(";")[0] if set_cookie else ""
        return status, payload, set_cookie, cookie

    # ---------- 测试 ----------

    def test_login_sets_httponly_cookie(self):
        status, payload, set_cookie, _cookie = self.login()
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=Strict", set_cookie)
        self.assertIn("vigil_session=", set_cookie)

    def test_api_requires_auth(self):
        status, payload, _set_cookie, _cookie = self.request("GET", "/api/status")
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "unauthorized")

        status, payload, _set_cookie, _cookie = self.request("GET", "/api/checks")
        self.assertEqual(status, 401)

    def test_bad_token_rejected(self):
        status, payload, _set_cookie, _cookie = self.login("wrong-token")
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "invalid_token")

    def test_login_rate_limit(self):
        with self.server.lock:
            self.server.login_failures.clear()
            self.server.login_max_failures = 2
        first = self.login("bad")[0]
        second = self.login("bad")[0]
        third = self.login("bad")[0]
        self.assertEqual(first, 401)
        self.assertEqual(second, 401)
        self.assertEqual(third, 429)

    def test_authenticated_status(self):
        _status, _payload, _set_cookie, cookie = self.login()
        status, payload, _set_cookie, _ctype = self.request(
            "GET", "/api/status", cookie=cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["agent_version"], "1.0.0")
        self.assertTrue(payload["registered"])

    def test_checks_logs_and_events_limit(self):
        _status, _payload, _set_cookie, cookie = self.login()

        status, payload, _set_cookie, _ctype = self.request(
            "GET", "/api/checks", cookie=cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload[0]["layer"], "net.carrier")

        status, payload, _set_cookie, _ctype = self.request(
            "GET", "/api/events?limit=1", cookie=cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(payload), 1)
        self.assertEqual(self.event_limits[-1], 1)

        status, payload, _set_cookie, _ctype = self.request(
            "GET", "/api/events?limit=abc", cookie=cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.event_limits[-1], 50)

        status, payload, _set_cookie, _ctype = self.request(
            "GET", "/api/logs?limit=3", cookie=cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.log_limits[-1], 3)

        status, payload, _set_cookie, _ctype = self.request(
            "GET", "/api/logs?level=warn&event=guard.&guard_id=abc&since=100",
            cookie=cookie,
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            self.log_filters,
            {
                "level": "warn",
                "event": "guard.",
                "guard_id": "abc",
                "recovery_id": None,
                "since": 100.0,
            },
        )

        status, body, headers = self.request_raw(
            "GET", "/api/logs/export?level=warn&event=guard.", cookie=cookie
        )
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers.get("Content-Disposition", ""))
        self.assertIn(b"guard.state", body)
        self.assertNotIn("limit", self.export_filters)

    def test_actions_require_auth_and_whitelist(self):
        status, _payload, _set_cookie, _ctype = self.request(
            "POST", "/api/actions/ifup_wan", body={}
        )
        self.assertEqual(status, 401)
        self.assertEqual(self.action_calls, [])

        _status, _payload, _set_cookie, cookie = self.login()

        status, payload, _set_cookie, _ctype = self.request(
            "POST", "/api/actions/ifup_wan", body={}, cookie=cookie
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["allowed"])
        self.assertEqual(payload["result"], "success")

        status, payload, _set_cookie, _ctype = self.request(
            "POST", "/api/actions/rm_rf", body={}, cookie=cookie
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["reason"], "not_whitelisted")

        status, payload, _set_cookie, _ctype = self.request(
            "POST",
            "/api/actions/restart_service",
            body={"target": "network"},
            cookie=cookie,
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["reason"], "target_not_whitelisted")

    def test_static_files_and_traversal(self):
        status, payload, _set_cookie, content_type = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Vigil UI", payload)
        self.assertIn("text/html", content_type)

        status, payload, _set_cookie, content_type = self.request(
            "GET", "/assets/app.js"
        )
        self.assertEqual(status, 200)
        self.assertIn("javascript", content_type)
        self.assertIn("console.log", payload)

        for path in (
            "/%2e%2e/%2e%2e/etc/passwd",
            "/..%2f..%2fetc%2fpasswd",
            "/assets/..%2f..%2f..%2fetc%2fpasswd",
        ):
            status, _payload, _set_cookie, _ctype = self.request("GET", path)
            self.assertIn(status, (400, 403, 404), "路径 %s 未被拦截" % path)

        status, _payload, _set_cookie, _ctype = self.request("GET", "/missing.js")
        self.assertEqual(status, 404)

    def test_spa_deep_link_falls_back_to_index(self):
        status, payload, _set_cookie, content_type = self.request("GET", "/guards")
        self.assertEqual(status, 200)
        self.assertIn("Vigil UI", payload)
        self.assertIn("text/html", content_type)

    def test_placeholder_when_www_missing(self):
        self.stop_server()
        self.start_server(os.path.join(self.root, "not-installed"))
        status, payload, _set_cookie, content_type = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Vigil", payload)
        self.assertIn("text/html", content_type)

    def test_unknown_api_path(self):
        _status, _payload, _set_cookie, cookie = self.login()
        status, payload, _set_cookie, _ctype = self.request(
            "GET", "/api/unknown", cookie=cookie
        )
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "not_found")

    def test_invalid_json_body(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(
            "POST",
            "/api/login",
            body=b"{broken",
            headers={"Content-Length": "7", "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
        self.assertEqual(response.status, 400)
        self.assertIn("JSON", payload["error"])


if __name__ == "__main__":
    unittest.main()
