"""内置 Probe 观测测试。"""

import json
import os
import socket
import tempfile
import unittest

from vigil_agent.probes.base import DetectContext
from vigil_agent.probes.builtin import (
    builtin_registry,
    carrier,
    command,
    dns,
    http,
    icmp,
    pppoe,
    process,
    route,
    tcp,
)
from vigil_agent.utils.proc import ProcessResult


class FakeRunner:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def run(self, args, timeout=None, env=None, cwd=None):
        self.calls.append(list(args))
        if not self.responses:
            return ProcessResult(exit_code=1, stderr="no response", latency_ms=1)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class ProbeRegistryTest(unittest.TestCase):
    def test_builtin_catalog(self):
        registry = builtin_registry()
        self.assertEqual(
            set(registry.ids()),
            {
                "carrier",
                "pppoe",
                "route",
                "dns",
                "http",
                "tcp",
                "process",
                "icmp",
                "command",
            },
        )
        self.assertNotIn("command", registry.remote_ids())
        self.assertIn("http", registry.remote_ids())


class CarrierProbeTest(unittest.TestCase):
    def test_reads_carrier_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "eth0"))
            with open(os.path.join(tmp, "eth0", "carrier"), "w", encoding="utf-8") as handle:
                handle.write("1")
            ctx = DetectContext(runner=FakeRunner(), sys_class_net=tmp)
            observation = carrier.PROBE.observe(ctx, {"device": "eth0"})
        self.assertEqual(observation.error, "")
        self.assertTrue(observation.data["up"])
        self.assertEqual(observation.data["device"], "eth0")

    def test_missing_carrier_file_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = DetectContext(runner=FakeRunner(), sys_class_net=tmp)
            observation = carrier.PROBE.observe(ctx, {"device": "eth0"})
        self.assertTrue(observation.error)


class PppoeProbeTest(unittest.TestCase):
    def test_ubus_status(self):
        payload = {
            "up": True,
            "uptime": 120,
            "proto": "pppoe",
            "l3_device": "pppoe-wan",
            "ipv4-address": [{"address": "1.2.3.4/32"}],
        }
        runner = FakeRunner([ProcessResult(0, json.dumps(payload))])
        ctx = DetectContext(runner=runner, timeout=3)
        observation = pppoe.PROBE.observe(ctx, {"interface": "wan"})
        self.assertEqual(observation.error, "")
        self.assertTrue(observation.data["up"])
        self.assertEqual(observation.data["ip"], "1.2.3.4")
        self.assertEqual(observation.data["uptime"], 120)

    def test_ubus_failure(self):
        runner = FakeRunner([ProcessResult(1, "", "not found")])
        ctx = DetectContext(runner=runner, timeout=3)
        observation = pppoe.PROBE.observe(ctx, {"interface": "wan"})
        self.assertTrue(observation.error)


class RouteProbeTest(unittest.TestCase):
    def test_default_route_and_egress(self):
        route_output = "default via 192.168.1.1 dev pppoe-wan proto static\n"
        ubus_payload = json.dumps(
            {"ipv4-address": [{"address": "100.64.0.2/32"}]}
        )
        runner = FakeRunner(
            [
                ProcessResult(0, route_output),
                ProcessResult(0, ubus_payload),
            ]
        )
        ctx = DetectContext(runner=runner, timeout=3)
        observation = route.PROBE.observe(ctx, {})
        self.assertEqual(observation.error, "")
        self.assertTrue(observation.data["has_route"])
        self.assertEqual(observation.data["egress_ip"], "100.64.0.2")

    def test_no_default_route(self):
        runner = FakeRunner([ProcessResult(0, "")])
        ctx = DetectContext(runner=runner, timeout=3)
        observation = route.PROBE.observe(ctx, {})
        self.assertFalse(observation.data["has_route"])


class DnsProbeTest(unittest.TestCase):
    def test_resolved(self):
        output = (
            "Server:\t\t127.0.0.1\n"
            "Address:\t127.0.0.1:53\n\n"
            "Name:\twww.baidu.com\n"
            "Address: 110.242.68.66\n"
        )
        runner = FakeRunner([ProcessResult(0, output)])
        ctx = DetectContext(runner=runner, timeout=3)
        observation = dns.PROBE.observe(ctx, {"name": "www.baidu.com"})
        self.assertEqual(observation.error, "")
        self.assertTrue(observation.data["resolved"])
        self.assertEqual(observation.data["address"], "110.242.68.66")

    def test_unresolved_is_observed_not_error(self):
        runner = FakeRunner([ProcessResult(2, "", "server can't find")])
        ctx = DetectContext(runner=runner, timeout=3)
        observation = dns.PROBE.observe(ctx, {"name": "nope.local"})
        self.assertEqual(observation.error, "")
        self.assertFalse(observation.data["resolved"])


class HttpProbeTest(unittest.TestCase):
    def test_uses_injected_fetch(self):
        calls = []

        def fake_get(url, timeout, method, headers):
            calls.append((url, timeout, method, headers))
            return 204, "", 12, ""

        ctx = DetectContext(http_get=fake_get, timeout=4)
        observation = http.PROBE.observe(
            ctx, {"url": "http://example.com/204", "method": "GET"}
        )
        self.assertEqual(observation.error, "")
        self.assertEqual(observation.data["status"], 204)
        self.assertEqual(observation.latency_ms, 12)
        self.assertEqual(calls[0][0], "http://example.com/204")

    def test_network_error_is_observation_error(self):
        ctx = DetectContext(
            http_get=lambda url, timeout, method, headers: (0, "", 5, "timeout"),
            timeout=4,
        )
        observation = http.PROBE.observe(ctx, {"url": "http://example.com"})
        self.assertTrue(observation.error)
        self.assertEqual(observation.data["status"], 0)


class TcpProbeTest(unittest.TestCase):
    def test_connects_to_listening_port(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            ctx = DetectContext(timeout=2)
            observation = tcp.PROBE.observe(
                ctx, {"host": "127.0.0.1", "port": port}
            )
        finally:
            server.close()
        self.assertEqual(observation.error, "")
        self.assertTrue(observation.data["connected"])

    def test_invalid_port_is_error(self):
        ctx = DetectContext(timeout=1)
        observation = tcp.PROBE.observe(ctx, {"host": "127.0.0.1", "port": 0})
        self.assertTrue(observation.error)


class ProcessProbeTest(unittest.TestCase):
    def test_procd_running(self):
        payload = {
            "dnsmasq": {
                "instances": {
                    "instance1": {"running": True, "pid": 1234},
                }
            }
        }
        runner = FakeRunner([ProcessResult(0, json.dumps(payload))])
        ctx = DetectContext(runner=runner, timeout=3)
        observation = process.PROBE.observe(ctx, {"service": "dnsmasq"})
        self.assertEqual(observation.error, "")
        self.assertTrue(observation.data["running"])
        self.assertEqual(observation.data["pids"], [1234])

    def test_pgrep_fallback(self):
        runner = FakeRunner([ProcessResult(2, "", "ubus not found")])
        runner.responses.append(ProcessResult(1, ""))
        ctx = DetectContext(runner=runner, timeout=3)
        observation = process.PROBE.observe(
            ctx, {"service": "dnsmasq", "process": "dnsmasq"}
        )
        self.assertEqual(observation.error, "")
        self.assertFalse(observation.data["running"])


class IcmpProbeTest(unittest.TestCase):
    def test_reachable(self):
        output = (
            "PING 1.1.1.1 (1.1.1.1): 56 data bytes\n"
            "64 bytes from 1.1.1.1: seq=0 ttl=58 time=10.5 ms\n"
            "--- 1.1.1.1 ping statistics ---\n"
            "1 packets transmitted, 1 packets received, 0% packet loss\n"
            "round-trip min/avg/max = 10.0/10.5/11.0 ms\n"
        )
        runner = FakeRunner([ProcessResult(0, output)])
        ctx = DetectContext(runner=runner, timeout=3)
        observation = icmp.PROBE.observe(ctx, {"host": "1.1.1.1"})
        self.assertTrue(observation.data["reachable"])
        self.assertEqual(observation.data["loss_percent"], 0.0)
        self.assertEqual(observation.data["rtt_ms"], 10.5)


class CommandProbeTest(unittest.TestCase):
    def test_command_runs_argv(self):
        runner = FakeRunner([ProcessResult(0, "hello")])
        ctx = DetectContext(runner=runner, timeout=3)
        observation = command.PROBE.observe(ctx, {"argv": ["echo", "hello"]})
        self.assertEqual(observation.error, "")
        self.assertEqual(observation.data["exit_code"], 0)
        self.assertIn("hello", observation.data["stdout"])
        self.assertEqual(runner.calls[0], ["echo", "hello"])
        self.assertFalse(command.PROBE.remote_allowed)


if __name__ == "__main__":
    unittest.main()
