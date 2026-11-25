"""模块管理：脚本探测器存储与本地 API 测试。"""

import http.client
import json
import os
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch

from vigil_agent.config import default_config
from vigil_agent.detect.script import delete_script, inspect_scripts, save_script
from vigil_agent.guards.model import migrate_config
from vigil_agent.httpd import create_server
from vigil_agent.main import AgentApp

BODY = "#!/bin/sh\necho '{\"evidence\":{\"ok\":true}}'\nexit 0\n"


class ScriptStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = os.path.join(self.tmp.name, "probes.d")
        os.makedirs(self.directory, mode=0o700)
        self.env = patch.dict(os.environ, {"VIGIL_PROBES_DIR": self.directory})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_save_and_inspect(self):
        entry = save_script("demo", BODY, timeout=12)
        self.assertTrue(entry["valid"])
        self.assertEqual(entry["timeout"], 12)
        path = os.path.join(self.directory, "demo.sh")
        self.assertTrue(os.path.isfile(path))
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(os.stat(self.directory).st_mode), 0o700)

        manifest = inspect_scripts()
        self.assertTrue(manifest["directory_ok"])
        self.assertEqual([item["name"] for item in manifest["scripts"]], ["demo"])
        self.assertEqual(manifest["unregistered"], [])

    def test_overwrite_updates_hash(self):
        first = save_script("demo", BODY, timeout=10)["sha256"]
        second = save_script("demo", "#!/bin/sh\nexit 0\n", timeout=5)
        self.assertNotEqual(first, second["sha256"])
        self.assertEqual(len(inspect_scripts()["scripts"]), 1)

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            save_script("../escape", BODY)
        with self.assertRaises(ValueError):
            save_script("bad name", BODY)
        with self.assertRaises(ValueError):
            save_script("empty", "   ")
        with self.assertRaises(ValueError):
            save_script("huge", "x" * 70000)

    def test_inspect_without_manifest_is_empty(self):
        inventory = inspect_scripts()
        self.assertTrue(inventory["directory_ok"])
        self.assertFalse(inventory["manifest_exists"])
        self.assertEqual(inventory["error"], "")
        self.assertEqual(inventory["scripts"], [])

    def test_delete_removes_file_and_entry(self):
        save_script("demo", BODY)
        delete_script("demo")
        self.assertFalse(os.path.exists(os.path.join(self.directory, "demo.sh")))
        self.assertEqual(inspect_scripts()["scripts"], [])
        with self.assertRaises(ValueError):
            delete_script("demo")

    def test_unregistered_files_are_listed(self):
        save_script("demo", BODY)
        with open(os.path.join(self.directory, "stray.sh"), "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\nexit 0\n")
        self.assertEqual(inspect_scripts()["unregistered"], ["stray.sh"])

    @unittest.skipIf(os.name == "nt", "Windows 不支持 0700 权限语义")
    def test_directory_permissions_enforced(self):
        os.chmod(self.directory, 0o755)
        self.assertIn("0700", inspect_scripts()["error"])
        with self.assertRaises(ValueError):
            save_script("demo", BODY)


class AgentModulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.directory = os.path.join(root, "probes.d")
        os.makedirs(self.directory, mode=0o700)
        self.env = patch.dict(os.environ, {"VIGIL_PROBES_DIR": self.directory})
        self.env.start()

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
        self.app = AgentApp(
            cfg, config_path=os.path.join(root, "config.json"), httpd_enabled=False
        )

    def tearDown(self):
        self.app.shutdown()
        self.env.stop()
        self.tmp.cleanup()

    def test_builtin_probe_list(self):
        payload = self.app.api_modules()
        ids = [item["id"] for item in payload["builtin"]]
        self.assertEqual(
            ids,
            ["carrier", "pppoe", "route", "dns", "http", "process", "port", "script"],
        )
        self.assertEqual(payload["scripts"]["scripts"], [])

    def test_import_and_delete_script(self):
        result = self.app.api_script_import(
            {"name": "demo", "content": BODY, "timeout": 8}
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["script"]["valid"])
        self.assertEqual(
            [item["name"] for item in result["modules"]["scripts"]["scripts"]], ["demo"]
        )

        deleted = self.app.api_script_delete("demo")
        self.assertTrue(deleted["ok"])
        self.assertEqual(deleted["modules"]["scripts"]["scripts"], [])

    def test_import_rejects_invalid_payload(self):
        with self.assertRaises(ValueError):
            self.app.api_script_import({"name": "demo"})
        with self.assertRaises(ValueError):
            self.app.api_script_delete("")


class HttpModulesApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.import_calls = []
        self.server = create_server(
            "127.0.0.1",
            0,
            "admin-token",
            os.path.join(self.tmp.name, "www"),
            modules_provider=self._modules,
            script_import_handler=self._import,
            script_delete_handler=self._delete,
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.tmp.cleanup()

    def _modules(self):
        return {"builtin": [{"id": "http"}], "scripts": {"scripts": []}}

    def _import(self, payload):
        self.import_calls.append(payload)
        if payload.get("name") == "bad":
            raise ValueError("name 非法")
        if payload.get("name") == "boom":
            raise RuntimeError("unexpected")
        return {"ok": True, "script": {"name": payload.get("name")}, "modules": self._modules()}

    def _delete(self, name):
        if name == "missing":
            raise ValueError("未注册的脚本探测器：missing")
        return {"ok": True, "modules": self._modules()}

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

    def test_get_requires_auth(self):
        status, payload, _cookie = self.request("GET", "/api/modules")
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "unauthorized")

    def test_get_returns_modules(self):
        cookie = self.login()
        status, payload, _cookie = self.request("GET", "/api/modules", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(payload["builtin"], [{"id": "http"}])

    def test_import_requires_auth_and_maps_errors(self):
        status, _payload, _cookie = self.request(
            "POST", "/api/modules/script", {"name": "demo", "content": "x"}
        )
        self.assertEqual(status, 401)
        self.assertEqual(self.import_calls, [])

        cookie = self.login()
        status, payload, _cookie = self.request(
            "POST",
            "/api/modules/script",
            {"name": "demo", "content": "x"},
            cookie=cookie,
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(self.import_calls[0]["name"], "demo")

        status, payload, _cookie = self.request(
            "POST", "/api/modules/script", {"name": "bad", "content": "x"}, cookie=cookie
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "name 非法")

        status, _payload, _cookie = self.request(
            "POST", "/api/modules/script", {"name": "boom", "content": "x"}, cookie=cookie
        )
        self.assertEqual(status, 500)

    def test_delete_name_validation(self):
        cookie = self.login()
        status, payload, _cookie = self.request(
            "POST", "/api/modules/script/delete", {}, cookie=cookie
        )
        self.assertEqual(status, 400)
        self.assertIn("name", payload["error"])

        status, payload, _cookie = self.request(
            "POST", "/api/modules/script/delete", {"name": "missing"}, cookie=cookie
        )
        self.assertEqual(status, 400)
        self.assertIn("未注册", payload["error"])

        status, payload, _cookie = self.request(
            "POST", "/api/modules/script/delete", {"name": "demo"}, cookie=cookie
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
