"""script 探测器测试。"""

import hashlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from vigil_agent.config import default_config
from vigil_agent.detect import script as script_probe
from vigil_agent.detect.base import CommandResult, DetectContext
from vigil_agent.guards.checks import run_checks


class FakeRunner:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def run(self, args, timeout=None, env=None):
        self.calls.append({"args": list(args), "timeout": timeout, "env": dict(env or {})})
        return self.result


def _write_probe(directory, *, sha256=None, body="#!/bin/sh\nexit 0\n", entry=None):
    script_path = os.path.join(directory, "demo.sh")
    with open(script_path, "w", encoding="utf-8", newline="") as handle:
        handle.write(body)
    if sha256 is None:
        sha256 = hashlib.sha256(body.encode("utf-8")).hexdigest()
    manifest = {
        "demo": entry
        if entry is not None
        else {"file": "demo.sh", "sha256": sha256, "timeout": 10}
    }
    with open(os.path.join(directory, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle)
    return script_path


def _context(runner, **params):
    return DetectContext(default_config(), runner=runner, params=params)


class ScriptProbeTest(unittest.TestCase):
    def test_ok_with_evidence_and_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_probe(tmp)
            runner = FakeRunner(
                CommandResult(
                    0,
                    stdout='{"evidence": {"value": 1}, "message": "fine"}',
                    elapsed_ms=12,
                )
            )
            with patch.dict(os.environ, {"VIGIL_PROBES_DIR": tmp}):
                result = script_probe.detect(_context(runner, name="demo", url="http://a"))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["evidence"]["value"], 1)
        self.assertEqual(result["evidence"]["message"], "fine")
        call = runner.calls[0]
        self.assertEqual(call["args"], [os.path.join(tmp, "demo.sh")])
        self.assertEqual(call["env"]["VIGIL_PROBE"], "demo")
        self.assertEqual(call["env"]["VIGIL_PARAM_URL"], "http://a")
        self.assertIn('"url": "http://a"', call["env"]["VIGIL_PARAMS"])

    def test_exit_code_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_probe(tmp)
            runner = FakeRunner(CommandResult(2, stdout=""))
            with patch.dict(os.environ, {"VIGIL_PROBES_DIR": tmp}):
                result = script_probe.detect(_context(runner, name="demo"))
        self.assertEqual(result["status"], "critical")
        self.assertEqual(result["evidence"]["reason"], "脚本退出码 2")

    def test_hash_mismatch_blocks_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_probe(tmp, sha256="0" * 64)
            runner = FakeRunner(CommandResult(0, stdout=""))
            with patch.dict(os.environ, {"VIGIL_PROBES_DIR": tmp}):
                result = script_probe.detect(_context(runner, name="demo"))
        self.assertEqual(result["status"], "unknown")
        self.assertIn("哈希校验失败", result["evidence"]["reason"])
        self.assertFalse(runner.calls)

    def test_missing_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner(CommandResult(0, stdout=""))
            with patch.dict(os.environ, {"VIGIL_PROBES_DIR": tmp}):
                result = script_probe.detect(_context(runner, name="demo"))
        self.assertEqual(result["status"], "unknown")
        self.assertIn("清单", result["evidence"]["reason"])

    def test_unknown_script_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_probe(tmp)
            runner = FakeRunner(CommandResult(0, stdout=""))
            with patch.dict(os.environ, {"VIGIL_PROBES_DIR": tmp}):
                result = script_probe.detect(_context(runner, name="missing"))
        self.assertEqual(result["status"], "unknown")
        self.assertIn("未注册", result["evidence"]["reason"])

    def test_stdout_must_be_single_line_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_probe(tmp)
            runner = FakeRunner(CommandResult(0, stdout='{"a": 1}\n{"b": 2}\n'))
            with patch.dict(os.environ, {"VIGIL_PROBES_DIR": tmp}):
                result = script_probe.detect(_context(runner, name="demo"))
        self.assertEqual(result["status"], "unknown")
        self.assertIn("单行 JSON", result["evidence"]["reason"])

    def test_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_probe(tmp)
            runner = FakeRunner(CommandResult(0, stdout="not-json"))
            with patch.dict(os.environ, {"VIGIL_PROBES_DIR": tmp}):
                result = script_probe.detect(_context(runner, name="demo"))
        self.assertEqual(result["status"], "unknown")
        self.assertIn("合法 JSON", result["evidence"]["reason"])

    def test_timeout_is_critical(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_probe(tmp)
            runner = FakeRunner(CommandResult(-9, timed_out=True, elapsed_ms=10000))
            with patch.dict(os.environ, {"VIGIL_PROBES_DIR": tmp}):
                result = script_probe.detect(_context(runner, name="demo"))
        self.assertEqual(result["status"], "critical")
        self.assertIn("超时", result["evidence"]["reason"])

    def test_missing_name(self):
        runner = FakeRunner(CommandResult(0))
        result = script_probe.detect(_context(runner))
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(runner.calls)

    @unittest.skipIf(os.name == "nt", "Windows 不支持 0700 权限语义")
    def test_directory_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_probe(tmp)
            os.chmod(tmp, 0o755)
            runner = FakeRunner(CommandResult(0, stdout=""))
            with patch.dict(os.environ, {"VIGIL_PROBES_DIR": tmp}):
                result = script_probe.detect(_context(runner, name="demo"))
        self.assertEqual(result["status"], "unknown")
        self.assertIn("0700", result["evidence"]["reason"])


class ScriptCheckRunnerTest(unittest.TestCase):
    def test_check_runner_registers_script_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_probe(tmp)
            runner = FakeRunner(CommandResult(0, stdout='{"evidence": {"ok": true}}'))
            guard = {"checks": [{"probe": "script", "params": {"name": "demo"}}]}
            with patch.dict(os.environ, {"VIGIL_PROBES_DIR": tmp}):
                results = run_checks(guard, default_config(), runner=runner)
        self.assertEqual(results["script"]["status"], "ok")
        self.assertTrue(results["script"]["evidence"]["ok"])
