"""本地 probe / 检测项 API 路由测试。"""

import http.client
import json
import os
import tempfile
import threading
import unittest

from vigil_agent.api import create_server


class ProbeApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.www = os.path.join(self.tmp.name, "www")
        os.makedirs(self.www)
        self.install_calls = []
        self.delete_calls = []
        self.test_calls = []
        self.check_calls = []

        self.server = create_server(
            "127.0.0.1",
            0,
            "admin-token",
            self.www,
            probes_provider=lambda: {
                "items": [{"id": "http", "source": "builtin"}],
                "directory": "/tmp/probes.d",
            },
            probes_install_handler=self._install,
            probes_delete_handler=self._delete,
            probes_test_handler=self._test,
            checks_test_handler=self._check_test,
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        try:
            self.server.shutdown()
        except Exception:
            pass
        try:
            self.server.server_close()
        except Exception:
            pass
        self.thread.join(2)
        self.tmp.cleanup()

    # ---------- 假处理函数 ----------

    def _install(self, data):
        self.install_calls.append(len(data))
        return {"ok": True, "size": len(data)}

    def _delete(self, probe_id):
        self.delete_calls.append(probe_id)
        if probe_id == "http":
            raise ValueError("非法的本地 probe id")
        return {"ok": True, "id": probe_id}

    def _test(self, probe_id, params, expect):
        self.test_calls.append((probe_id, params, expect))
        return {
            "status": "ok",
            "probe_id": probe_id,
            "params": params or {},
            "expect": expect or {},
        }

    def _check_test(self, check_id):
        self.check_calls.append(check_id)
        return {"status": "ok", "check_id": check_id}

    # ---------- 辅助 ----------

    def _login(self):
        status, payload, set_cookie = self._request(
            "POST", "/api/login", json_body={"token": "admin-token"}
        )
        self.assertEqual(status, 200)
        return set_cookie.split(";")[0]

    def _request(self, method, path, json_body=None, raw_body=None, headers=None, cookie=""):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        request_headers = dict(headers or {})
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
            request_headers["Content-Length"] = str(len(body))
        elif raw_body is not None:
            body = raw_body
            request_headers.setdefault("Content-Length", str(len(body)))
        if cookie:
            request_headers["Cookie"] = cookie
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        raw = response.read()
        status = response.status
        set_cookie = response.getheader("Set-Cookie") or ""
        connection.close()
        payload = None
        if raw:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except ValueError:
                payload = raw
        return status, payload, set_cookie

    # ---------- 测试 ----------

    def test_probes_require_auth(self):
        status, payload, _cookie = self._request("GET", "/api/probes")
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "unauthorized")

    def test_list_probes(self):
        cookie = self._login()
        status, payload, _set_cookie = self._request(
            "GET", "/api/probes", cookie=cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["items"][0]["id"], "http")
        self.assertEqual(payload["directory"], "/tmp/probes.d")

    def test_install_probe_with_gzip(self):
        cookie = self._login()
        data = b"\x1f\x8b\x08\x00" + b"fake-archive"
        status, payload, _set_cookie = self._request(
            "POST",
            "/api/probes",
            raw_body=data,
            headers={"Content-Type": "application/gzip"},
            cookie=cookie,
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(self.install_calls, [len(data)])

    def test_install_rejects_plain_body(self):
        cookie = self._login()
        status, payload, _set_cookie = self._request(
            "POST",
            "/api/probes",
            raw_body=b"not-gzip",
            headers={"Content-Type": "application/gzip"},
            cookie=cookie,
        )
        self.assertEqual(status, 400)
        self.assertEqual(self.install_calls, [])

    def test_delete_probe(self):
        cookie = self._login()
        status, payload, _set_cookie = self._request(
            "DELETE", "/api/probes/local.demo", cookie=cookie
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

        status, payload, _set_cookie = self._request(
            "DELETE", "/api/probes/http", cookie=cookie
        )
        self.assertEqual(status, 400)
        self.assertEqual(self.delete_calls, ["local.demo", "http"])

    def test_probe_test(self):
        cookie = self._login()
        status, payload, _set_cookie = self._request(
            "POST",
            "/api/probes/local.demo/test",
            json_body={"params": {"value": "x"}, "expect": {"kind": "json"}},
            cookie=cookie,
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(self.test_calls[0][1], {"value": "x"})

    def test_probe_test_rejects_bad_params(self):
        cookie = self._login()
        status, payload, _set_cookie = self._request(
            "POST",
            "/api/probes/local.demo/test",
            json_body={"params": "bad"},
            cookie=cookie,
        )
        self.assertEqual(status, 400)
        self.assertEqual(self.test_calls, [])

    def test_check_test(self):
        cookie = self._login()
        status, payload, _set_cookie = self._request(
            "POST", "/api/checks/wan-link/test", json_body={}, cookie=cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["check_id"], "wan-link")
        self.assertEqual(self.check_calls, ["wan-link"])


if __name__ == "__main__":
    unittest.main()
