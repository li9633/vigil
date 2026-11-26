"""本地 probe 包：manifest 校验、安装删除与子进程执行测试。"""

import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
import uuid

from vigil_agent.app import AgentApp
from vigil_agent.checks.model import normalize_check
from vigil_agent.config import default_config
from vigil_agent.guards.model import normalize_guard
from vigil_agent.probes.base import DetectContext
from vigil_agent.probes.builtin import builtin_registry
from vigil_agent.probes.loader import (
    LocalProbeStore,
    ProbePackageError,
    read_manifest,
)

ECHO_SCRIPT = """import json
import sys

payload = json.load(sys.stdin)
data = {
    "echo": payload.get("params") or {},
    "check_id": payload.get("check_id") or "",
}
print(json.dumps({"data": data, "latency_ms": 7, "error": None}))
"""


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_manifest(probe_id="local.demo", content=ECHO_SCRIPT, **overrides):
    data = {
        "schema_version": 1,
        "id": probe_id,
        "name": "演示探测器",
        "version": "1.0.0",
        "description": "回显参数",
        "entry": "probe.py",
        "interpreter": "python3",
        "timeout": 5,
        "sha256": _sha256(content),
        "params_schema": {"value": {"type": "string", "label": "值"}},
        "default_expect": {"kind": "json", "path": "echo.value", "equals": "x"},
    }
    data.update(overrides)
    return data


def write_package(directory, manifest=None, content=ECHO_SCRIPT, dir_name=None):
    name = dir_name or (manifest or {}).get("id", "local.demo")
    root = os.path.join(directory, name)
    os.makedirs(root, exist_ok=True)
    with open(
        os.path.join(root, "probe.py"), "w", encoding="utf-8", newline="\n"
    ) as handle:
        handle.write(content)
    with open(os.path.join(root, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest or make_manifest(content=content), handle)
    return root


def make_archive(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in files.items():
            raw = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    return buffer.getvalue()


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = os.path.join(self.tmp.name, "probes.d")
        os.makedirs(self.directory)
        self.store = LocalProbeStore(self.directory)

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_package_registers_as_local(self):
        write_package(self.directory)
        packages = self.store.scan()
        self.assertEqual(len(packages), 1)
        self.assertTrue(packages[0].valid)
        self.assertEqual(packages[0].id, "local.demo")

        registry = builtin_registry()
        errors = self.store.load_into(registry)
        self.assertEqual(errors, [])
        probe = registry.get("local.demo")
        self.assertIsNotNone(probe)
        self.assertFalse(probe.remote_allowed)
        self.assertEqual(probe.catalog_entry()["source"], "local")

    def test_directory_name_must_match_id(self):
        write_package(self.directory, dir_name="wrong-name")
        packages = self.store.scan()
        self.assertEqual(len(packages), 1)
        self.assertFalse(packages[0].valid)
        self.assertIn("目录名", packages[0].error)

    def test_sha256_mismatch_is_rejected(self):
        write_package(self.directory, make_manifest(sha256="0" * 64))
        package = self.store.scan()[0]
        self.assertFalse(package.valid)
        self.assertIn("sha256", package.error)

    def test_interpreter_and_timeout_validated(self):
        write_package(
            self.directory,
            make_manifest(interpreter="bash", timeout=99),
        )
        package = self.store.scan()[0]
        self.assertFalse(package.valid)
        self.assertIn("interpreter", package.error)
        self.assertIn("timeout", package.error)

    def test_default_expect_kind_validated(self):
        write_package(self.directory, make_manifest(default_expect={"kind": "nope"}))
        package = self.store.scan()[0]
        self.assertFalse(package.valid)
        self.assertIn("default_expect", package.error)

    def test_read_manifest_rejects_missing_entry(self):
        root = write_package(self.directory)
        os.unlink(os.path.join(root, "probe.py"))
        with self.assertRaises(ProbePackageError):
            read_manifest(root, "local.demo")


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = os.path.join(self.tmp.name, "probes.d")
        self.store = LocalProbeStore(self.directory)

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_tar_gz(self):
        manifest = make_manifest()
        data = make_archive(
            {
                "local.demo/manifest.json": json.dumps(manifest),
                "local.demo/probe.py": ECHO_SCRIPT,
            }
        )
        package = self.store.install(data)
        self.assertEqual(package.id, "local.demo")
        self.assertTrue(
            os.path.isfile(os.path.join(self.directory, "local.demo", "probe.py"))
        )
        self.assertTrue(self.store.scan()[0].valid)

    def test_install_rejects_path_traversal(self):
        manifest = make_manifest()
        data = make_archive(
            {
                "local.demo/manifest.json": json.dumps(manifest),
                "local.demo/probe.py": ECHO_SCRIPT,
                "../evil.py": "print('x')",
            }
        )
        with self.assertRaises(ProbePackageError):
            self.store.install(data)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "evil.py")))

    def test_install_rejects_non_gzip(self):
        with self.assertRaises(ProbePackageError):
            self.store.install(b"not a gzip")

    def test_remove_local_and_builtin(self):
        write_package(self.directory)
        self.store.remove("local.demo")
        self.assertEqual(self.store.scan(), [])
        with self.assertRaises(LookupError):
            self.store.remove("local.demo")
        with self.assertRaises(ValueError):
            self.store.remove("http")


class LocalProbeRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = os.path.join(self.tmp.name, "probes.d")
        os.makedirs(self.directory)
        self.store = LocalProbeStore(self.directory)

    def tearDown(self):
        self.tmp.cleanup()

    def _registry(self, content=ECHO_SCRIPT, **overrides):
        write_package(
            self.directory,
            make_manifest(content=content, **overrides),
            content=content,
        )
        registry = builtin_registry()
        errors = self.store.load_into(registry)
        self.assertEqual(errors, [])
        return registry

    def _ctx(self, params=None, timeout=5):
        return DetectContext(params=params or {}, timeout=timeout, check_id="c1")

    def test_executes_and_returns_observation(self):
        probe = self._registry().get("local.demo")
        observation = probe.observe(self._ctx({"value": "x"}), {"value": "x"})
        self.assertEqual(observation.error, "")
        self.assertEqual(observation.data["echo"], {"value": "x"})
        self.assertEqual(observation.data["check_id"], "c1")
        self.assertEqual(observation.latency_ms, 7)

    def test_sha_rechecked_before_each_run(self):
        registry = self._registry()
        probe = registry.get("local.demo")
        with open(os.path.join(self.directory, "local.demo", "probe.py"), "a") as handle:
            handle.write("\n# changed\n")
        observation = probe.observe(self._ctx(), {})
        self.assertIn("SHA-256", observation.error)

    def test_invalid_json_output(self):
        registry = self._registry(content="print('not-json')\n")
        probe = registry.get("local.demo")
        observation = probe.observe(self._ctx(), {})
        self.assertIn("JSON", observation.error)

    def test_nonzero_exit(self):
        registry = self._registry(
            content="import sys\nsys.stderr.write('boom')\nsys.exit(2)\n"
        )
        probe = registry.get("local.demo")
        observation = probe.observe(self._ctx(), {})
        self.assertIn("退出码", observation.error)
        self.assertIn("boom", observation.data["stderr"])

    def test_timeout(self):
        registry = self._registry(content="import time\ntime.sleep(3)\n")
        probe = registry.get("local.demo")
        observation = probe.observe(self._ctx(timeout=0.3), {})
        self.assertIn("超时", observation.error)


class AgentAppProbeTest(unittest.TestCase):
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
        cfg["probes"]["dir"] = os.path.join(self.root, "probes.d")
        cfg["httpd"]["token"] = "admin-token"
        self.app = AgentApp(cfg, config_path=self.config_path, httpd_enabled=False)

    def tearDown(self):
        self.app.shutdown()
        self.tmp.cleanup()

    def _archive(self):
        manifest = make_manifest()
        return make_archive(
            {
                "local.demo/manifest.json": json.dumps(manifest),
                "local.demo/probe.py": ECHO_SCRIPT,
            }
        )

    def test_install_test_and_delete(self):
        payload = self.app.api_probe_install(self._archive())
        ids = [item["id"] for item in payload["items"]]
        self.assertIn("local.demo", ids)
        self.assertIn("http", ids)

        result = self.app.api_probe_test("local.demo", {"value": "x"})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["observation"]["data"]["echo"], {"value": "x"})
        self.assertTrue(result["probe"]["remote_allowed"] is False)

        payload = self.app.api_probe_delete("local.demo")
        ids = [item["id"] for item in payload["items"]]
        self.assertNotIn("local.demo", ids)

    def test_probe_test_rejects_unknown_probe(self):
        with self.assertRaises(LookupError):
            self.app.api_probe_test("local.missing", {})

    def test_checks_include_definition_fields(self):
        items = self.app.api_checks()
        first = items[0]
        self.assertEqual(first["id"], "wan-link")
        self.assertEqual(first["probe"], "carrier")
        self.assertFalse(first["has_result"])
        self.assertFalse(first["probe_missing"])
        self.assertIn("expect", first)
        self.assertIn("depends_on", first)

    def test_local_probe_drives_check_status(self):
        self.app.api_probe_install(self._archive())
        check = normalize_check(
            {
                "id": "local-echo",
                "probe": "local.demo",
                "params": {"value": "x"},
                "expect": {"kind": "json", "path": "echo.value", "equals": "x"},
                "timeout": 5,
            },
            self.app.registry,
        )
        guard = normalize_guard(
            {
                "id": str(uuid.uuid4()),
                "name": "本地检查",
                "check_refs": ["local-echo"],
                "policy": {"interval": 30},
                "recovery": [],
            }
        )
        with self.app._detect_lock:
            self.app.checks = [check]
            self.app.guards = [guard]
            self.app.state.set_checks({})
            self.app.state.set_guards({})
            self.app._guard_last_run.clear()
        self.app.guard_fsm = self.app.guard_fsm.__class__(
            state_path=os.path.join(self.root, "guards-local.json")
        )
        results = self.app.detect_once(emit_events=False, allow_actions=False)
        self.assertEqual(results["local-echo"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
