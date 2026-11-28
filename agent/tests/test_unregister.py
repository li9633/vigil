"""客户端取消注册测试：服务端确认与离线清理。"""

import json
import os
import tempfile
import unittest

from vigil_agent.app import AgentApp
from vigil_agent.config import default_config
from vigil_agent.services import pairing_service


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
    path = os.path.join(root, "config.json")
    return AgentApp(cfg, config_path=path, httpd_enabled=False), path


class UnregisterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app, self.config_path = _build_app(self.tmp.name)
        self.app.config["server"]["client_id"] = "cl_test"
        self.app.config["server"]["secret"] = "s" * 40
        self.app.config["server"]["bootstrap_token"] = "boot-token"
        self.app.transport.update_credentials("cl_test", "s" * 40)
        pairing_service.save_state(
            self.app,
            {
                "request_id": "req-1",
                "claim_token": "claim-1",
                "code": "ABCD-1234",
                "status": "pending",
                "code_expires_at": 0,
                "expires_at": 0,
                "server_url": "http://192.0.2.10:5000",
                "last_poll": 0,
                "last_error": "",
                "updated_at": 0,
            },
        )

    def tearDown(self):
        self.app.shutdown()
        self.tmp.cleanup()

    def _assert_local_cleared(self):
        server = self.app.config["server"]
        self.assertEqual(server["client_id"], "")
        self.assertEqual(server["secret"], "")
        self.assertEqual(server["bootstrap_token"], "")
        self.assertFalse(self.app.transport.is_registered)
        self.assertFalse(os.path.exists(pairing_service.state_path(self.app)))
        with open(self.config_path, encoding="utf-8") as handle:
            stored = json.load(handle)
        self.assertEqual(stored["server"]["client_id"], "")
        self.assertEqual(stored["server"]["secret"], "")
        self.assertEqual(stored["server"]["bootstrap_token"], "")

    def test_unregister_notifies_server_and_clears_local_state(self):
        called = []

        def fake_unregister():
            called.append(True)
            return {"ok": True, "status": 200, "body": {}}

        self.app.transport.unregister = fake_unregister
        result = self.app.api_unregister()

        self.assertTrue(called)
        self.assertTrue(result["server_notified"])
        self.assertEqual(result["server_error"], "")
        self.assertFalse(result["settings"]["server"]["registered"])
        self.assertEqual(result["settings"]["pairing"]["status"], "idle")
        self._assert_local_cleared()

    def test_unregister_offline_still_clears_local_state(self):
        self.app.transport.unregister = lambda: {
            "ok": False,
            "status": 0,
            "body": {},
            "error": "连接超时",
        }
        result = self.app.api_unregister()

        self.assertFalse(result["server_notified"])
        self.assertIn("连接超时", result["server_error"])
        self._assert_local_cleared()

    def test_unregister_treats_gone_server_as_notified(self):
        self.app.transport.unregister = lambda: {
            "ok": False,
            "status": 404,
            "body": {},
            "error": "HTTP 404",
        }
        result = self.app.api_unregister()

        self.assertTrue(result["server_notified"])
        self._assert_local_cleared()


if __name__ == "__main__":
    unittest.main()
