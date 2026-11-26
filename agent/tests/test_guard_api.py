"""本地 Guard 编辑 API 与服务端下发热更新测试。"""

import copy
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
import uuid

from vigil_agent.api import create_server
from vigil_agent.app import AgentApp
from vigil_agent.config import default_config


def make_guard(name="测试守护", interval=30, enabled=True, check_refs=None):
    """构造一个可通过 validate_guards 的最小 Guard。"""
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "template": "",
        "enabled": enabled,
        "check_refs": list(check_refs or ["wan-link"]),
        "policy": {"interval": interval},
        "recovery": [{"action": "ifup_wan"}],
        "limits": {"max_risk_level": "L1"},
    }


def make_check(check_id="custom-http", probe="http", params=None):
    return {
        "id": check_id,
        "name": check_id,
        "probe": probe,
        "params": params if params is not None else {"url": "http://127.0.0.1/"},
        "expect": {"kind": "http", "status": [200]},
        "timeout": 5,
        "depends_on": [],
        "enabled": True,
    }


class FakeTransport:
    """只实现 fetch_config 的假传输层，用于驱动 poll_config。"""

    def __init__(self, body):
        self.body = body
        self.calls = []

    def fetch_config(self, etag=None):
        self.calls.append(etag)
        return {
            "ok": True,
            "status": 200,
            "etag": '"v%d"' % len(self.calls),
            "body": copy.deepcopy(self.body),
            "headers": {},
            "error": "",
        }


class AgentAppGuardTest(unittest.TestCase):
    """AgentApp 层的 Guard 读写、服务端下发与热更新。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.config_path = os.path.join(self.root, "config.json")

        cfg = default_config()
        cfg["storage"] = {
            "state_path": os.path.join(self.root, "state.json"),
            "spool_dir": os.path.join(self.root, "spool"),
            "www_root": os.path.join(self.root, "www"),
            "run_dir": os.path.join(self.root, "run"),
            "log_path": os.path.join(self.root, "vigil.log"),
            "lock_path": os.path.join(self.root, "vigil.lock"),
        }
        cfg["httpd"]["token"] = "admin-token"
        cfg["guards"] = [make_guard(name="初始守护", interval=30)]

        self.app = AgentApp(cfg, config_path=self.config_path, httpd_enabled=False)
        self.initial_guard = self.app.config["guards"][0]

    def tearDown(self):
        self.app.shutdown()
        self.tmp.cleanup()

    # ---------- GET ----------

    def test_api_guards_defaults_to_local_editable(self):
        payload = self.app.api_guards()
        self.assertEqual(payload["source"], "local")
        self.assertTrue(payload["editable"])
        self.assertEqual([item["id"] for item in payload["items"]], [self.initial_guard["id"]])

    def test_api_status_exposes_guard_source(self):
        status = self.app.api_status()
        self.assertEqual(status["guards_source"], "local")
        self.assertTrue(status["guards_editable"])

    # ---------- PUT ----------

    def test_put_updates_guards_persists_and_reschedules(self):
        new_guard = make_guard(name="新的守护", interval=10)
        result = self.app.api_guards_update([new_guard])

        self.assertEqual(result["source"], "local")
        self.assertTrue(result["editable"])
        self.assertEqual([item["id"] for item in result["items"]], [new_guard["id"]])
        self.assertEqual([item["id"] for item in self.app.guards], [new_guard["id"]])
        self.assertEqual([item["id"] for item in self.app.config["guards"]], [new_guard["id"]])
        self.assertEqual(self.app._guard_last_run, {})

        with open(self.config_path, encoding="utf-8") as handle:
            persisted = json.load(handle)
        self.assertEqual([item["id"] for item in persisted["guards"]], [new_guard["id"]])

        # 调度节拍取各 Guard 间隔的最小值
        self.assertEqual(self.app.scheduler._tasks["detect"]["interval"], 10)

        event_types = [item["type"] for item in self.app.state.events_snapshot(50)]
        self.assertIn("config_updated", event_types)

    def test_put_invalid_guards_raises_chinese_error(self):
        before = copy.deepcopy(self.app.guards)
        with self.assertRaises(ValueError) as ctx:
            self.app.api_guards_update([{"id": "not-a-uuid", "name": ""}])
        message = str(ctx.exception)
        self.assertIn("id", message)
        self.assertTrue(any("\u4e00" <= char <= "\u9fff" for char in message))
        self.assertEqual(self.app.guards, before)

    def test_put_empty_list_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.app.api_guards_update([])
        self.assertIn("guards", str(ctx.exception))

    def test_put_rejected_when_guards_managed_by_server(self):
        self.app.guards_source = "server"
        with self.assertRaises(ValueError) as ctx:
            self.app.api_guards_update([make_guard()])
        self.assertEqual(str(ctx.exception), "managed_by_server")

    # ---------- 服务端下发 ----------

    def test_poll_config_applies_server_guards(self):
        server_guard = make_guard(name="服务端守护", interval=15)
        self.app.transport = FakeTransport(
            {"schema_version": 3, "guards": [server_guard]}
        )

        self.app.poll_config()

        self.assertEqual(self.app.guards_source, "server")
        self.assertFalse(self.app.api_guards()["editable"])
        self.assertEqual([item["id"] for item in self.app.guards], [server_guard["id"]])
        self.assertEqual(
            [item["id"] for item in self.app._server_guards], [server_guard["id"]]
        )
        self.assertEqual(self.app.scheduler._tasks["detect"]["interval"], 15)
        self.assertEqual(self.app._guard_last_run, {})

    def test_poll_config_without_guards_falls_back_to_local(self):
        self.app.transport = FakeTransport(
            {"schema_version": 3, "guards": [make_guard(name="服务端守护")]}
        )
        self.app.poll_config()
        self.assertEqual(self.app.guards_source, "server")

        local_guard = make_guard(name="本地守护", interval=20)
        self.app.config["guards"] = [local_guard]
        self.app.transport = FakeTransport({"other": True})

        self.app.poll_config()

        self.assertEqual(self.app.guards_source, "local")
        self.assertIsNone(self.app._server_guards)
        self.assertEqual([item["id"] for item in self.app.guards], [local_guard["id"]])
        self.assertEqual(self.app.scheduler._tasks["detect"]["interval"], 20)

    def test_poll_config_invalid_guards_keeps_current(self):
        before = copy.deepcopy(self.app.guards)
        self.app.transport = FakeTransport(
            {"schema_version": 3, "guards": [{"id": "not-a-uuid", "name": ""}]}
        )
        with self.assertLogs("vigil.agent", level="WARNING"):
            self.app.poll_config()
        self.assertEqual(self.app.guards, before)
        self.assertEqual(self.app.guards_source, "local")

    def test_poll_config_applies_server_checks_and_guards(self):
        check = make_check()
        server_guard = make_guard(name="服务端守护", interval=15, check_refs=[check["id"]])
        self.app.transport = FakeTransport(
            {"schema_version": 3, "checks": [check], "guards": [server_guard]}
        )

        self.app.poll_config()

        self.assertEqual(self.app.guards_source, "server")
        self.assertFalse(self.app.api_guards()["editable"])
        self.assertEqual([item["id"] for item in self.app.checks], [check["id"]])
        self.assertEqual([item["id"] for item in self.app.guards], [server_guard["id"]])
        self.assertEqual(self.app.scheduler._tasks["detect"]["interval"], 15)

        # 服务端撤回配置后回退本地 checks + guards
        self.app.transport = FakeTransport({"schema_version": 3})
        self.app.poll_config()
        self.assertEqual(self.app.guards_source, "local")
        self.assertIn("wan-link", [item["id"] for item in self.app.checks])
        self.assertEqual(
            [item["id"] for item in self.app.guards], [self.initial_guard["id"]]
        )
        self.assertIsNone(self.app._server_guards)

    def test_poll_config_invalid_server_checks_keeps_current(self):
        before_checks = copy.deepcopy(self.app.checks)
        before_guards = copy.deepcopy(self.app.guards)
        self.app.transport = FakeTransport(
            {
                "schema_version": 3,
                "checks": [make_check("local-cmd", probe="command", params={"argv": ["true"]})],
                "guards": [make_guard(check_refs=["local-cmd"])],
            }
        )
        with self.assertLogs("vigil.agent", level="WARNING"):
            self.app.poll_config()
        self.assertEqual(self.app.checks, before_checks)
        self.assertEqual(self.app.guards, before_guards)
        self.assertEqual(self.app.guards_source, "local")

    def test_poll_config_guard_refs_must_match_server_checks(self):
        before_checks = copy.deepcopy(self.app.checks)
        check = make_check()
        self.app.transport = FakeTransport(
            {
                "schema_version": 3,
                "checks": [check],
                "guards": [make_guard(name="断链", check_refs=["wan-link"])],
            }
        )
        with self.assertLogs("vigil.agent", level="WARNING"):
            self.app.poll_config()
        self.assertEqual(self.app.checks, before_checks)
        self.assertEqual(self.app.guards_source, "local")

    # ---------- 热更新即时生效 ----------

    def test_hot_update_takes_effect_immediately(self):
        self.app._guard_last_run[self.initial_guard["id"]] = time.time()

        new_guard = make_guard(name="热更新守护", interval=5)
        self.app.api_guards_update([new_guard])

        # 上一轮运行时间已重置，下一轮立即重新执行
        self.assertEqual(self.app._guard_last_run, {})
        snapshots = self.app._evaluate_guards(now=time.time())
        self.assertIn(new_guard["id"], snapshots)
        self.assertEqual(snapshots[new_guard["id"]]["name"], "热更新守护")
        self.assertNotIn(self.initial_guard["id"], snapshots)


class HttpGuardApiTest(unittest.TestCase):
    """HTTP 路由层：鉴权、PUT 请求校验与状态码映射。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.put_calls = []
        self.server = create_server(
            "127.0.0.1",
            0,
            "admin-token",
            os.path.join(self.tmp.name, "www"),
            guards_provider=self._guards_provider,
            guards_handler=self._guards_handler,
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

    def _guards_provider(self):
        return {"items": [{"id": "g1"}], "source": "local", "editable": True}

    def _guards_handler(self, guards):
        self.put_calls.append(copy.deepcopy(guards))
        names = [item.get("name") for item in guards if isinstance(item, dict)]
        if "managed" in names:
            raise ValueError("managed_by_server")
        if "bad" in names:
            raise ValueError("守护项名称不能为空")
        if "boom" in names:
            raise RuntimeError("unexpected")
        return {"items": guards, "source": "local", "editable": True}

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
        payload = None
        if raw:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except ValueError:
                payload = raw.decode("utf-8", "replace")
        return status, payload, set_cookie

    def login(self):
        _status, _payload, set_cookie = self.request(
            "POST", "/api/login", {"token": "admin-token"}
        )
        return set_cookie.split(";")[0]

    def test_get_requires_auth(self):
        status, payload, _set_cookie = self.request("GET", "/api/guards")
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "unauthorized")

    def test_get_returns_provider_payload(self):
        cookie = self.login()
        status, payload, _set_cookie = self.request("GET", "/api/guards", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(payload["items"], [{"id": "g1"}])
        self.assertEqual(payload["source"], "local")
        self.assertTrue(payload["editable"])

    def test_put_requires_auth(self):
        status, _payload, _set_cookie = self.request(
            "PUT", "/api/guards", body={"guards": []}
        )
        self.assertEqual(status, 401)
        self.assertEqual(self.put_calls, [])

    def test_put_passes_guards_to_handler(self):
        cookie = self.login()
        guards = [{"id": "g1", "name": "ok"}]
        status, payload, _set_cookie = self.request(
            "PUT", "/api/guards", body={"guards": guards}, cookie=cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.put_calls, [guards])
        self.assertEqual(payload["items"], guards)

    def test_put_managed_by_server_returns_409(self):
        cookie = self.login()
        status, payload, _set_cookie = self.request(
            "PUT",
            "/api/guards",
            body={"guards": [{"name": "managed"}]},
            cookie=cookie,
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "managed_by_server")

    def test_put_invalid_guard_returns_400(self):
        cookie = self.login()
        status, payload, _set_cookie = self.request(
            "PUT", "/api/guards", body={"guards": [{"name": "bad"}]}, cookie=cookie
        )
        self.assertEqual(status, 400)
        self.assertIn("名称", payload["error"])

    def test_put_non_list_returns_400(self):
        cookie = self.login()
        status, payload, _set_cookie = self.request(
            "PUT", "/api/guards", body={"guards": "oops"}, cookie=cookie
        )
        self.assertEqual(status, 400)
        self.assertIn("数组", payload["error"])
        self.assertEqual(self.put_calls, [])

    def test_put_invalid_json_returns_400(self):
        cookie = self.login()
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(
            "PUT",
            "/api/guards",
            body=b"{broken",
            headers={
                "Content-Length": "7",
                "Content-Type": "application/json",
                "Cookie": cookie,
            },
        )
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
        self.assertEqual(response.status, 400)
        self.assertIn("JSON", payload["error"])

    def test_put_unexpected_error_returns_500(self):
        cookie = self.login()
        with self.assertLogs("vigil.httpd", level="ERROR"):
            status, payload, _set_cookie = self.request(
                "PUT", "/api/guards", body={"guards": [{"name": "boom"}]}, cookie=cookie
            )
        self.assertEqual(status, 500)
        self.assertEqual(payload["error"], "internal_error")


if __name__ == "__main__":
    unittest.main()
