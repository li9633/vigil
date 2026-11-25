"""客户端设置 API 测试。"""

import http.client
import json
import os
import tempfile
import threading
import time
import unittest

from vigil_agent.config import default_config
from vigil_agent.guards.model import migrate_config
from vigil_agent.httpd import create_server
from vigil_agent.main import AgentApp


def _build_app(root):
    cfg = default_config()
    cfg["storage"] = {
        "state_path": os.path.join(root, "state.json"),
        "spool_dir": os.path.join(root, "spool"),
        "www_root": os.path.join(root, "www"),
        "run_dir": os.path.join(root, "run"),
        "log_path": os.path.join(root, "vigil.log"),
        "lock_path": os.path.join(root, "vigil.lock"),
    }
    cfg["httpd"]["token"] = "admin-token"
    migrate_config(cfg)
    path = os.path.join(root, "config.json")
    return AgentApp(cfg, config_path=path, httpd_enabled=False), path


class SettingsApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app, self.config_path = _build_app(self.tmp.name)

    def tearDown(self):
        self.app.shutdown()
        self.tmp.cleanup()

    def test_snapshot_shape(self):
        payload = self.app.api_settings()
        self.assertIn("version", payload)
        self.assertIn("heartbeat_interval", payload["server"])
        self.assertIn("max_actions_per_hour", payload["action_safety"])
        self.assertFalse(payload["maintenance"]["active"])

    def test_update_server_url_and_heartbeat(self):
        payload = self.app.api_settings_update(
            {
                "server": {"base_url": "http://192.0.2.10:5000/"},
                "heartbeat_interval": 60,
            }
        )
        self.assertEqual(self.app.transport.base_url, "http://192.0.2.10:5000")
        self.assertEqual(payload["server"]["heartbeat_interval"], 60)
        self.assertEqual(self.app.scheduler._tasks["heartbeat"]["interval"], 60.0)

        with open(self.config_path, "r", encoding="utf-8") as handle:
            stored = json.load(handle)
        self.assertEqual(stored["server"]["base_url"], "http://192.0.2.10:5000")
        self.assertEqual(stored["server"]["heartbeat_interval"], 60)

    def test_invalid_values_are_rejected(self):
        with self.assertRaises(ValueError):
            self.app.api_settings_update({"server": {"base_url": "ftp://example.com"}})
        with self.assertRaises(ValueError):
            self.app.api_settings_update({"heartbeat_interval": 5})
        with self.assertRaises(ValueError):
            self.app.api_settings_update({"heartbeat_interval": 301})
        with self.assertRaises(ValueError):
            self.app.api_settings_update({"action_safety": {"max_risk_level": "L2"}})
        with self.assertRaises(ValueError):
            self.app.api_settings_update({"action_safety": {"max_actions_per_hour": 0}})
        with self.assertRaises(ValueError):
            self.app.api_settings_update({"maintenance_minutes": 1441})

    def test_update_action_safety(self):
        payload = self.app.api_settings_update(
            {
                "action_safety": {
                    "global_enabled": False,
                    "max_risk_level": "L1",
                    "max_actions_per_hour": 5,
                }
            }
        )
        self.assertFalse(payload["action_safety"]["global_enabled"])
        self.assertFalse(self.app.executor.enabled)
        self.assertEqual(self.app.executor.max_per_hour, 5)
        self.assertEqual(self.app.config["action_safety"]["max_actions_per_hour"], 5)

    def test_maintenance_window(self):
        payload = self.app.api_settings_update({"maintenance_minutes": 30})
        self.assertTrue(payload["maintenance"]["active"])
        self.assertGreater(payload["maintenance"]["until"], int(time.time()))

        payload = self.app.api_settings_update({"maintenance_minutes": 0})
        self.assertFalse(payload["maintenance"]["active"])
        self.assertLessEqual(payload["maintenance"]["until"], int(time.time()))


class HttpSettingsApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app, _path = _build_app(self.tmp.name)
        self.server = create_server(
            "127.0.0.1",
            0,
            "admin-token",
            os.path.join(self.tmp.name, "www"),
            settings_provider=self.app.api_settings,
            settings_handler=self.app.api_settings_update,
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.app.shutdown()
        self.tmp.cleanup()

    def request(self, method, path, body=None, cookie=""):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(data))
        if cookie:
            headers["Cookie"] = cookie
        connection.request(method, path, body=data, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        status = response.status
        set_cookie = response.getheader("Set-Cookie") or ""
        connection.close()
        payload = json.loads(raw.decode("utf-8")) if raw else None
        return status, payload, set_cookie

    def login(self):
        _status, _payload, set_cookie = self.request(
            "POST", "/api/login", {"token": "admin-token"}
        )
        return set_cookie.split(";")[0]

    def test_settings_requires_auth(self):
        status, payload, _cookie = self.request("GET", "/api/settings")
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "unauthorized")

    def test_get_and_update(self):
        cookie = self.login()
        status, payload, _cookie = self.request("GET", "/api/settings", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn("version", payload)

        status, payload, _cookie = self.request(
            "PUT",
            "/api/settings",
            {"heartbeat_interval": 45},
            cookie=cookie,
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["server"]["heartbeat_interval"], 45)

        status, payload, _cookie = self.request(
            "PUT",
            "/api/settings",
            {"server": {"base_url": "not-a-url"}},
            cookie=cookie,
        )
        self.assertEqual(status, 400)
        self.assertIn("http", payload["error"])
