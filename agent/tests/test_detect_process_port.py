"""process / port 探测器测试。"""

import socket
import unittest

from vigil_agent.config import default_config
from vigil_agent.detect import port as port_probe
from vigil_agent.detect import process as process_probe
from vigil_agent.detect.base import CommandResult, DetectContext
from vigil_agent.guards.checks import run_checks


class SequenceRunner:
    """按顺序返回预置结果的命令执行器替身。"""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def run(self, args, timeout=None):
        self.calls.append((list(args), timeout))
        if len(self.results) == 1:
            return self.results[0]
        return self.results.pop(0)


def _context(runner, **params):
    return DetectContext(default_config(), runner=runner, params=params)


class ProcessProbeTest(unittest.TestCase):
    def test_procd_service_running(self):
        payload = '{"dnsmasq": {"instances": {"instance1": {"running": true, "pid": 321}}}}'
        runner = SequenceRunner(CommandResult(0, stdout=payload, elapsed_ms=7))
        result = process_probe.detect(_context(runner, service="dnsmasq"))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["evidence"]["pids"], [321])
        self.assertEqual(result["evidence"]["source"], "procd")
        self.assertEqual(runner.calls[0][0][:3], ["ubus", "call", "service"])

    def test_procd_service_stopped(self):
        payload = '{"dnsmasq": {"instances": {"instance1": {"running": false}}}}'
        runner = SequenceRunner(CommandResult(0, stdout=payload))
        result = process_probe.detect(_context(runner, service="dnsmasq"))
        self.assertEqual(result["status"], "critical")
        self.assertEqual(result["evidence"]["reason"], "procd 服务未运行")

    def test_procd_missing_service(self):
        runner = SequenceRunner(CommandResult(0, stdout="{}"))
        result = process_probe.detect(_context(runner, service="missing"))
        self.assertEqual(result["status"], "critical")
        self.assertFalse(result["evidence"]["running"])

    def test_fallback_to_pgrep_when_ubus_unavailable(self):
        runner = SequenceRunner(
            CommandResult(127, stderr="ubus: not found"),
            CommandResult(0, stdout="42\n"),
        )
        result = process_probe.detect(
            _context(runner, service="dnsmasq", process="dnsmasq")
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["evidence"]["source"], "pgrep")
        self.assertEqual(result["evidence"]["pids"], [42])
        self.assertIn("ubus", result["evidence"]["fallback_reason"])

    def test_missing_params_is_unknown(self):
        runner = SequenceRunner(CommandResult(0))
        result = process_probe.detect(_context(runner))
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(runner.calls)

    def test_check_runner_registers_process_probe(self):
        payload = '{"dnsmasq": {"instances": {"instance1": {"running": true, "pid": 9}}}}'
        runner = SequenceRunner(CommandResult(0, stdout=payload))
        guard = {"checks": [{"probe": "process", "params": {"service": "dnsmasq"}}]}
        results = run_checks(guard, default_config(), runner=runner)
        self.assertEqual(results["process"]["status"], "ok")
        self.assertEqual(results["process"]["evidence"]["pids"], [9])


class PortProbeTest(unittest.TestCase):
    def test_open_port(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        try:
            target = listener.getsockname()[1]
            result = port_probe.detect(
                _context(None, host="127.0.0.1", port=target)
            )
        finally:
            listener.close()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["evidence"]["port"], target)

    def test_closed_port(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        target = probe.getsockname()[1]
        probe.close()
        result = port_probe.detect(_context(None, host="127.0.0.1", port=target))
        self.assertEqual(result["status"], "critical")
        self.assertTrue(result["evidence"]["reason"])

    def test_invalid_port_is_unknown(self):
        result = port_probe.detect(_context(None, host="127.0.0.1", port="abc"))
        self.assertEqual(result["status"], "unknown")

    def test_check_runner_registers_port_probe(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        try:
            target = listener.getsockname()[1]
            guard = {
                "checks": [
                    {
                        "probe": "port",
                        "params": {"host": "127.0.0.1", "port": target},
                    }
                ]
            }
            results = run_checks(guard, default_config())
        finally:
            listener.close()
        self.assertEqual(results["port"]["status"], "ok")
