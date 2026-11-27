"""客户端配对状态机测试：发起、轮询领取、拒绝与取消。"""

import json
import os
import tempfile
import time
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


class PairingFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app, self.config_path = _build_app(self.tmp.name)
        self.created = []

    def tearDown(self):
        self.app.shutdown()
        self.tmp.cleanup()

    def _fake_create(self, instance_id, name, code, model="", fw_version="", protocol=None):
        self.created.append(
            {"instance_id": instance_id, "name": name, "code": code, "model": model}
        )
        now = int(time.time())
        return {
            "ok": True,
            "status": 200,
            "body": {
                "request_id": "req-1",
                "claim_token": "claim-token-1",
                "server_time": now,
                "expires_at": now + 3600,
                "code_expires_at": now + 600,
            },
        }

    def test_start_shows_code_and_persists_secret_state(self):
        self.app.transport.create_pairing = self._fake_create
        payload = self.app.api_pairing_start("http://192.0.2.10:5000/")

        pairing = payload["pairing"]
        self.assertEqual(pairing["status"], "pending")
        self.assertRegex(pairing["code"], r"^[0-9A-Z]{4}-[0-9A-Z]{4}$")
        self.assertEqual(self.app.transport.base_url, "http://192.0.2.10:5000")
        self.assertEqual(self.created[0]["code"], pairing["code"])

        with open(pairing_service.state_path(self.app), encoding="utf-8") as handle:
            state = json.load(handle)
        self.assertEqual(state["claim_token"], "claim-token-1")
        if os.name != "nt":
            mode = os.stat(pairing_service.state_path(self.app)).st_mode & 0o777
            self.assertEqual(mode, 0o600)

        with open(self.config_path, encoding="utf-8") as handle:
            stored = json.load(handle)
        self.assertEqual(stored["server"]["base_url"], "http://192.0.2.10:5000")
        # claim_token 只存在于配对状态文件，不进入主配置
        self.assertNotIn("claim_token", json.dumps(stored))

    def test_poll_approved_claims_credentials(self):
        self.app.transport.create_pairing = self._fake_create
        self.app.api_pairing_start("http://192.0.2.10:5000")

        self.app.transport.pairing_status = lambda request_id, token: {
            "ok": True,
            "status": 200,
            "body": {
                "status": "approved",
                "client_id": "cl_test",
                "secret": "s" * 40,
                "heartbeat_interval": 45,
                "server_time": int(time.time()),
            },
        }
        payload = self.app.api_pairing_poll()

        self.assertTrue(payload["server"]["registered"])
        self.assertEqual(payload["pairing"]["status"], "paired")
        self.assertEqual(self.app.transport.client_id, "cl_test")
        self.assertEqual(self.app.scheduler._tasks["heartbeat"]["interval"], 45.0)
        self.assertFalse(os.path.exists(pairing_service.state_path(self.app)))

        with open(self.config_path, encoding="utf-8") as handle:
            stored = json.load(handle)
        self.assertEqual(stored["server"]["client_id"], "cl_test")
        self.assertEqual(stored["server"]["secret"], "s" * 40)

    def test_poll_denied_clears_claim_token(self):
        self.app.transport.create_pairing = self._fake_create
        self.app.api_pairing_start("http://192.0.2.10:5000")
        self.app.transport.pairing_status = lambda request_id, token: {
            "ok": True,
            "status": 200,
            "body": {"status": "denied", "reason": "来源不可信"},
        }

        payload = self.app.api_pairing_poll()
        self.assertEqual(payload["pairing"]["status"], "denied")
        self.assertEqual(payload["pairing"]["last_error"], "来源不可信")

        with open(pairing_service.state_path(self.app), encoding="utf-8") as handle:
            state = json.load(handle)
        self.assertEqual(state["claim_token"], "")

    def test_cancel_clears_local_state(self):
        self.app.transport.create_pairing = self._fake_create
        self.app.api_pairing_start("http://192.0.2.10:5000")
        payload = self.app.api_pairing_cancel()
        self.assertEqual(payload["pairing"]["status"], "idle")
        self.assertFalse(os.path.exists(pairing_service.state_path(self.app)))

    def test_start_rejects_invalid_url(self):
        with self.assertRaises(ValueError):
            self.app.api_pairing_start("ftp://example.com")


if __name__ == "__main__":
    unittest.main()
