"""M8-5 全链路联调：本地 probe → Check → Guard → 恢复动作。

用真实组件（probe 包安装、子进程 probe、CheckRunner、Guard 状态机、RecoveryRunner）
验证一次故障从观测到自愈的完整过程，仅把动作命令替换为可断言的假执行器。
"""

import hashlib
import io
import json
import os
import tarfile
import tempfile
import time
import unittest
import uuid

from vigil_agent.app import AgentApp
from vigil_agent.config import default_config
from vigil_agent.probes.loader import LocalProbeStore
from vigil_agent.utils.proc import ProcessResult

# 首次执行返回不健康并落盘标记；恢复后的复检读到标记返回健康。
PROBE_SCRIPT = """import json
import os
import sys

payload = json.load(sys.stdin)
marker = os.path.join(os.path.dirname(__file__), "recovered.flag")
healthy = os.path.exists(marker)
if not healthy:
    with open(marker, "w", encoding="utf-8") as handle:
        handle.write("1")
print(json.dumps({
    "data": {"healthy": healthy, "check_id": payload.get("check_id") or ""},
    "latency_ms": 2,
    "error": None,
}))
"""


class FakeActionRunner:
    """记录动作命令并返回成功，避免在开发机执行 ifup。"""

    def __init__(self):
        self.calls = []

    def run(self, args, timeout=None, **kwargs):
        self.calls.append(list(args))
        return ProcessResult(exit_code=0, latency_ms=3)


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _manifest(probe_id="local.health"):
    return {
        "schema_version": 1,
        "id": probe_id,
        "name": "联调健康探测",
        "version": "1.0.0",
        "description": "首次失败、复检成功",
        "entry": "probe.py",
        "interpreter": "python3",
        "timeout": 5,
        "sha256": _sha256(PROBE_SCRIPT),
        "params_schema": {},
        "default_expect": {"kind": "json", "path": "healthy", "equals": True},
    }


def _archive():
    buffer = io.BytesIO()
    files = {
        "local.health/manifest.json": json.dumps(_manifest()),
        "local.health/probe.py": PROBE_SCRIPT,
    }
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in files.items():
            raw = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    return buffer.getvalue()


class CustomProbeRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.probes_dir = os.path.join(self.root, "probes.d")
        os.makedirs(self.probes_dir)

        store = LocalProbeStore(self.probes_dir)
        self.package = store.install(_archive())
        self.assertEqual(self.package.id, "local.health")

        self.guard_id = str(uuid.uuid4())
        config = default_config()
        config["probes"]["dir"] = self.probes_dir
        config["server"].update(
            {
                "base_url": "",
                "client_id": "",
                "secret": "",
                "bootstrap_token": "",
                "heartbeat_interval": 30,
                "config_poll_interval": 300,
                "verify_tls": False,
            }
        )
        config["checks"] = [
            {
                "id": "custom-health",
                "name": "联调健康",
                "probe": "local.health",
                "params": {},
                "expect": {"kind": "json", "path": "healthy", "equals": True},
                "timeout": 5,
                "depends_on": [],
                "enabled": True,
            }
        ]
        config["guards"] = [
            {
                "id": self.guard_id,
                "name": "联调自愈",
                "template": "",
                "enabled": True,
                "check_refs": ["custom-health"],
                "evaluate": {"mode": "dependency_tree", "quorum": 1},
                "policy": {
                    "interval": 1,
                    "fail_threshold": 1,
                    "fail_mode": "consecutive",
                    "fail_window": 90,
                    "success_threshold": 1,
                    "cooldown": 0,
                    "max_actions_per_hour": 3,
                    "gave_up_after": 3,
                },
                "recovery": [{"action": "ifup_wan", "verify_after": 0}],
                "limits": {"max_risk_level": "L1"},
            }
        ]
        config["action_safety"] = {
            "global_enabled": True,
            "max_risk_level": "L1",
            "max_actions_per_hour": 3,
        }
        config["action"] = {
            "cooldown_seconds": 0,
            "allowed_services": ["dnsmasq"],
            "timeout": 5,
            "lock_path": os.path.join(self.root, "action.lock"),
            "wan_interface": "wan",
        }
        config["storage"] = {
            "state_path": os.path.join(self.root, "state.json"),
            "spool_dir": os.path.join(self.root, "spool"),
            "www_root": os.path.join(self.root, "www"),
            "run_dir": os.path.join(self.root, "run"),
            "log_path": os.path.join(self.root, "vigil.log"),
            "log_max_bytes": 262144,
            "log_backups": 1,
            "log_compress": False,
            "lock_path": os.path.join(self.root, "vigil.lock"),
        }
        config["httpd"]["token"] = "test-token"
        self.app = AgentApp(
            config,
            config_path=os.path.join(self.root, "config.json"),
            httpd_enabled=False,
        )
        self.action_runner = FakeActionRunner()
        self.app.executor.runner = self.action_runner
        self.app.executor.use_lock = False
        self.app.executor.cooldown_seconds = 0
        self.app.executor.state_path = os.path.join(self.root, "actions.json")

    def tearDown(self):
        thread = self.app._action_thread
        if thread is not None:
            thread.join(5.0)
        self.app.shutdown()
        self.tmp.cleanup()

    def _wait_recovery(self, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            thread = self.app._action_thread
            if thread is None:
                return
            thread.join(0.05)
        self.fail("恢复线程未在 %.1fs 内结束" % timeout)

    def test_custom_probe_check_guard_recovery(self):
        first = self.app.detect_once()
        self.assertEqual(first["custom-health"]["status"], "critical")
        self.assertEqual(first["custom-health"]["evidence"]["reason"], "json_value_mismatch")
        self.assertEqual(self.app.state.snapshot_guards()[self.guard_id]["status"], "FAILED")

        self._wait_recovery()

        # 动作命令由假执行器接收
        self.assertEqual(self.action_runner.calls, [["ifup", "wan"]])
        guard = self.app.state.snapshot_guards()[self.guard_id]
        self.assertEqual(guard["status"], "OK")
        self.assertEqual(guard["recovery_rounds"], 0)

        events = self.app.state.events_snapshot(50)
        types = [item.get("type") for item in events]
        self.assertIn("check_status", types)
        self.assertIn("action_result", types)
        self.assertIn("guard_recovery", types)
        recovery_events = [
            item
            for item in events
            if item.get("type") == "guard_recovery"
            and item.get("payload", {}).get("guard_id") == self.guard_id
        ]
        self.assertEqual(len(recovery_events), 1)
        self.assertTrue(recovery_events[0]["payload"]["recovered"])

        # 下一轮检测读到 probe 标记，Check 与 Guard 都恢复
        second = self.app.detect_once()
        self.assertEqual(second["custom-health"]["status"], "ok")
        self.assertEqual(self.app.state.snapshot_guards()[self.guard_id]["status"], "OK")


if __name__ == "__main__":
    unittest.main()
