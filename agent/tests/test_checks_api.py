"""本地检测项编辑：整表替换、Guard 引用校验与持久化。"""

import json
import os
import tempfile
import unittest
import uuid

from vigil_agent.app import AgentApp
from vigil_agent.config import default_config


def make_check(check_id="wan-link"):
    return {
        "id": check_id,
        "name": check_id,
        "probe": "http",
        "params": {"url": "http://127.0.0.1/"},
        "expect": {"kind": "http", "status": [200]},
        "timeout": 5,
        "depends_on": [],
        "enabled": True,
    }


def make_guard(check_refs):
    return {
        "id": str(uuid.uuid4()),
        "name": "测试守护",
        "template": "",
        "enabled": True,
        "check_refs": list(check_refs),
        "policy": {"interval": 10},
        "recovery": [{"action": "ifup_wan"}],
        "limits": {"max_risk_level": "L1"},
    }


class LocalCheckEditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.config_path = os.path.join(self.root, "config.json")
        config = default_config()
        config["storage"] = {
            "state_path": os.path.join(self.root, "state.json"),
            "spool_dir": os.path.join(self.root, "spool"),
            "www_root": os.path.join(self.root, "www"),
            "run_dir": os.path.join(self.root, "run"),
            "log_path": os.path.join(self.root, "vigil.log"),
            "lock_path": os.path.join(self.root, "vigil.lock"),
        }
        config["httpd"]["token"] = "admin-token"
        config["checks"] = [make_check("wan-link")]
        config["guards"] = [make_guard(["wan-link"])]
        self.app = AgentApp(config, config_path=self.config_path, httpd_enabled=False)

    def tearDown(self):
        self.app.shutdown()
        self.tmp.cleanup()

    def test_update_checks_replaces_and_persists(self):
        added = make_check("api-health")
        result = self.app.api_checks_update([make_check("wan-link"), added])
        self.assertEqual([item["id"] for item in result], ["wan-link", "api-health"])

        with open(self.config_path, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual([item["id"] for item in saved["checks"]], ["wan-link", "api-health"])
        self.assertEqual(self.app.config["checks"][1]["params"]["url"], "http://127.0.0.1/")

    def test_update_cannot_remove_guard_reference(self):
        with self.assertRaises(ValueError) as ctx:
            self.app.api_checks_update([make_check("other")])
        self.assertIn("wan-link", str(ctx.exception))
        self.assertEqual([item["id"] for item in self.app.checks], ["wan-link"])

    def test_update_rejects_unknown_probe(self):
        broken = make_check("broken")
        broken["probe"] = "not-installed"
        with self.assertRaises(ValueError):
            self.app.api_checks_update([broken])


if __name__ == "__main__":
    unittest.main()
