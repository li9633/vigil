"""检测模块与状态聚合测试（全部使用注入的假命令执行器）。"""

import json
import os
import sys
import tempfile
import unittest

from vigil_agent.config import default_config
from vigil_agent.detect import carrier, dns, http, pppoe, route
from vigil_agent.detect.base import (
    CommandResult,
    CommandRunner,
    DetectContext,
    DetectionEngine,
    DetectorRegistry,
    make_result,
)


class FakeRunner:
    def __init__(self, responses=None):
        self.responses = dict(responses or {})
        self.calls = []

    def run(self, args, timeout=None):
        key = " ".join(args)
        self.calls.append(key)
        result = self.responses.get(key)
        if result is None:
            return CommandResult(exit_code=1, stderr="未预置命令：" + key)
        if callable(result):
            return result()
        return result


def ubus_payload(up=True, uptime=100, l3_device="eth0", ip="203.0.113.5"):
    payload = {
        "up": up,
        "uptime": uptime,
        "l3_device": l3_device,
        "device": l3_device,
        "proto": "pppoe",
        "ipv4-address": [{"address": ip, "mask": 24}] if ip else [],
    }
    return json.dumps(payload)


def build_ctx(root, responses=None, detect=None, http_get=None):
    cfg = default_config()
    if detect:
        cfg["detect"].update(detect)
    runner = FakeRunner(responses)
    ctx = DetectContext(
        cfg,
        runner=runner,
        sys_class_net=root,
        http_get=http_get,
    )
    return ctx, runner


def write_carrier(root, device, value):
    directory = os.path.join(root, device)
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "carrier"), "w", encoding="utf-8") as handle:
        handle.write(value + "\n")


class CarrierTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_explicit_device_uses_no_command(self):
        write_carrier(self.root, "eth0", "1")
        ctx, runner = build_ctx(self.root, detect={"wan_device": "eth0"})
        result = carrier.detect(ctx)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["evidence"]["source"], "config")
        self.assertEqual(runner.calls, [])

    def test_auto_resolves_from_uci_first(self):
        write_carrier(self.root, "pppoe-wan", "1")
        ctx, runner = build_ctx(
            self.root,
            responses={
                "uci get network.wan.device": CommandResult(0, "pppoe-wan\n"),
            },
        )
        result = carrier.detect(ctx)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["evidence"]["device"], "pppoe-wan")
        self.assertEqual(result["evidence"]["source"], "uci")
        self.assertEqual(runner.calls[0], "uci get network.wan.device")

    def test_auto_falls_back_to_ubus_l3_device(self):
        write_carrier(self.root, "eth0", "0")
        ctx, runner = build_ctx(
            self.root,
            responses={
                "uci get network.wan.device": CommandResult(1, stderr="not found"),
                "ubus call network.interface.wan status": CommandResult(
                    0, ubus_payload(l3_device="eth0")
                ),
            },
        )
        result = carrier.detect(ctx)
        self.assertEqual(result["status"], "critical")
        self.assertEqual(result["evidence"]["device"], "eth0")
        self.assertEqual(result["evidence"]["source"], "ubus")

    def test_missing_carrier_file_is_critical(self):
        ctx, _runner = build_ctx(self.root, detect={"wan_device": "eth9"})
        result = carrier.detect(ctx)
        self.assertEqual(result["status"], "critical")
        self.assertIn("error", result["evidence"])

    def test_unresolvable_device_is_unknown(self):
        ctx, _runner = build_ctx(self.root)
        result = carrier.detect(ctx)
        self.assertEqual(result["status"], "unknown")


class PppoeTest(unittest.TestCase):
    def _detect(self, status_result):
        ctx, _runner = build_ctx(
            ".",
            responses={
                "ubus call network.interface.wan status": status_result,
            },
        )
        return pppoe.detect(ctx)

    def test_down_is_critical(self):
        result = self._detect(CommandResult(0, ubus_payload(up=False, uptime=0)))
        self.assertEqual(result["status"], "critical")
        self.assertFalse(result["evidence"]["up"])

    def test_up_without_uptime_is_warn(self):
        result = self._detect(CommandResult(0, ubus_payload(up=True, uptime=0)))
        self.assertEqual(result["status"], "warn")

    def test_up_with_uptime_is_ok(self):
        result = self._detect(CommandResult(0, ubus_payload(up=True, uptime=436)))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["evidence"]["uptime"], 436)

    def test_ubus_failure_is_unknown(self):
        result = self._detect(CommandResult(1, stderr="ubus not found"))
        self.assertEqual(result["status"], "unknown")


class RouteTest(unittest.TestCase):
    def test_default_route_and_ip_is_ok(self):
        ctx, _runner = build_ctx(
            ".",
            responses={
                "ip -4 route show default": CommandResult(
                    0, "default via 203.0.113.1 dev eth0 proto static\n"
                ),
                "ubus call network.interface.wan status": CommandResult(
                    0, ubus_payload(ip="203.0.113.5")
                ),
            },
        )
        result = route.detect(ctx)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["evidence"]["egress_ip"], "203.0.113.5")
        self.assertIn("default via", result["evidence"]["default_route"])

    def test_missing_default_route_is_critical(self):
        ctx, _runner = build_ctx(
            ".",
            responses={
                "ip -4 route show default": CommandResult(0, ""),
            },
        )
        result = route.detect(ctx)
        self.assertEqual(result["status"], "critical")

    def test_route_without_ip_is_warn(self):
        ctx, _runner = build_ctx(
            ".",
            responses={
                "ip -4 route show default": CommandResult(
                    0, "default via 203.0.113.1 dev eth0\n"
                ),
                "ubus call network.interface.wan status": CommandResult(
                    0, ubus_payload(ip="")
                ),
                "ip -4 -o addr show dev eth0": CommandResult(0, ""),
            },
        )
        result = route.detect(ctx)
        self.assertEqual(result["status"], "warn")

    def test_ip_falls_back_to_ip_command(self):
        ctx, _runner = build_ctx(
            ".",
            responses={
                "ip -4 route show default": CommandResult(
                    0, "default via 203.0.113.1 dev eth0\n"
                ),
                "ubus call network.interface.wan status": CommandResult(1, stderr="no ubus"),
                "ip -4 -o addr show dev eth0": CommandResult(
                    0, "2: eth0    inet 203.0.113.9/24 brd 203.0.113.255 scope global eth0\n"
                ),
            },
        )
        result = route.detect(ctx)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["evidence"]["egress_ip"], "203.0.113.9")

    def test_ip_command_timeout_is_unknown(self):
        ctx, _runner = build_ctx(
            ".",
            responses={
                "ip -4 route show default": CommandResult(
                    -9, timed_out=True, error="timeout"
                ),
            },
        )
        result = route.detect(ctx)
        self.assertEqual(result["status"], "unknown")


class DnsTest(unittest.TestCase):
    NSLOOKUP_OK = (
        "Server:    127.0.0.1\n"
        "Address:   127.0.0.1:53\n\n"
        "Name:      www.baidu.com\n"
        "Address:   110.242.68.66\n"
    )

    def _detect(self, result):
        ctx, _runner = build_ctx(
            ".",
            responses={"nslookup www.baidu.com 127.0.0.1": result},
        )
        return dns.detect(ctx)

    def test_resolved_is_ok(self):
        result = self._detect(CommandResult(0, self.NSLOOKUP_OK))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["evidence"]["address"], "110.242.68.66")

    def test_failure_exit_code_is_critical(self):
        result = self._detect(CommandResult(2, stderr="can't resolve"))
        self.assertEqual(result["status"], "critical")
        self.assertEqual(result["evidence"]["exit_code"], 2)

    def test_timeout_is_critical(self):
        result = self._detect(CommandResult(-9, timed_out=True))
        self.assertEqual(result["status"], "critical")
        self.assertIn("超时", result["evidence"]["error"])

    def test_no_address_is_warn(self):
        result = self._detect(CommandResult(0, "Server: 127.0.0.1\n"))
        self.assertEqual(result["status"], "warn")


class HttpTest(unittest.TestCase):
    def _detect(self, responses, probes=None):
        sequence = list(responses)

        def fake_http_get(url, timeout):
            return sequence.pop(0)

        detect_cfg = {}
        if probes is not None:
            detect_cfg["http_probes"] = probes
        ctx, _runner = build_ctx(".", detect=detect_cfg, http_get=fake_http_get)
        return http.detect(ctx)

    def test_204_is_ok(self):
        result = self._detect([(204, 12, "")])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["latency_ms"], 12)

    def test_200_is_warn(self):
        result = self._detect([(200, 30, "")])
        self.assertEqual(result["status"], "warn")

    def test_all_unreachable_is_critical(self):
        result = self._detect([(0, 3000, "timed out")])
        self.assertEqual(result["status"], "critical")
        self.assertEqual(len(result["evidence"]["attempts"]), 1)

    def test_second_probe_recovers(self):
        result = self._detect(
            [(0, 3000, "timed out"), (204, 15, "")],
            probes=["http://a.example/204", "http://b.example/204"],
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["evidence"]["attempts"]), 2)

    def test_no_probes_is_unknown(self):
        ctx, _runner = build_ctx(".", detect={"http_probes": []})
        result = http.detect(ctx)
        self.assertEqual(result["status"], "unknown")


class AggregationTest(unittest.TestCase):
    def test_three_consecutive_failures_become_critical(self):
        flag = {"status": "critical"}
        registry = DetectorRegistry()
        registry.register("x", lambda ctx: make_result("x", flag["status"]))
        engine = DetectionEngine(registry=registry, failure_threshold=3)
        ctx = DetectContext(default_config())

        statuses = [engine.run(ctx)["x"]["status"] for _ in range(4)]
        self.assertEqual(statuses, ["warn", "warn", "critical", "critical"])
        self.assertEqual(engine.run(ctx)["x"]["evidence"]["consecutive_failures"], 5)

    def test_success_resets_streak(self):
        flag = {"status": "critical"}
        registry = DetectorRegistry()
        registry.register("x", lambda ctx: make_result("x", flag["status"]))
        engine = DetectionEngine(registry=registry, failure_threshold=3)
        ctx = DetectContext(default_config())

        engine.run(ctx)
        engine.run(ctx)
        flag["status"] = "ok"
        self.assertEqual(engine.run(ctx)["x"]["status"], "ok")
        self.assertEqual(engine.streaks()["x"], 0)

        flag["status"] = "critical"
        self.assertEqual(engine.run(ctx)["x"]["status"], "warn")

    def test_child_is_skipped_when_parent_critical(self):
        registry = DetectorRegistry()
        registry.register("parent", lambda ctx: make_result("parent", "critical"))
        registry.register(
            "child", lambda ctx: make_result("child", "ok"), depends_on=("parent",)
        )
        engine = DetectionEngine(registry=registry, failure_threshold=3)
        ctx = DetectContext(default_config())

        first = engine.run(ctx)
        second = engine.run(ctx)
        third = engine.run(ctx)
        self.assertEqual(first["child"]["status"], "ok")
        self.assertEqual(second["child"]["status"], "ok")
        self.assertEqual(third["parent"]["status"], "critical")
        self.assertEqual(third["child"]["status"], "skipped")
        self.assertEqual(third["child"]["evidence"]["blocked_by"], "parent")

    def test_cycle_detection(self):
        registry = DetectorRegistry()
        registry.register("a", lambda ctx: make_result("a", "ok"), depends_on=("b",))
        registry.register("b", lambda ctx: make_result("b", "ok"), depends_on=("a",))
        with self.assertRaises(ValueError):
            registry.ordered()


class FullChainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        write_carrier(self.root, "eth0", "1")

    def tearDown(self):
        self.tmp.cleanup()

    def _responses(self, up=True, uptime=100):
        return {
            "ubus call network.interface.wan status": CommandResult(
                0, ubus_payload(up=up, uptime=uptime)
            ),
            "ip -4 route show default": CommandResult(
                0, "default via 203.0.113.1 dev eth0\n"
            ),
            "nslookup www.baidu.com 127.0.0.1": CommandResult(
                0, DnsTest.NSLOOKUP_OK
            ),
        }

    def _ctx(self, up=True):
        return DetectContext(
            default_config(),
            runner=FakeRunner(self._responses(up=up)),
            sys_class_net=self.root,
            http_get=lambda url, timeout: (204, 10, ""),
        )

    def test_full_chain_ok(self):
        engine = DetectionEngine()
        results = engine.run(self._ctx())
        self.assertEqual(
            [results[layer]["status"] for layer in results],
            ["ok", "ok", "ok", "ok", "ok"],
        )

    def test_pppoe_failure_skips_downstream_after_threshold(self):
        engine = DetectionEngine()
        engine.run(self._ctx(up=False))
        engine.run(self._ctx(up=False))
        third = engine.run(self._ctx(up=False))
        self.assertEqual(third["net.carrier"]["status"], "ok")
        self.assertEqual(third["net.pppoe"]["status"], "critical")
        for layer in ("net.route", "net.dns", "net.http"):
            self.assertEqual(third[layer]["status"], "skipped")


class CommandRunnerTest(unittest.TestCase):
    def test_timeout_kills_process(self):
        runner = CommandRunner()
        result = runner.run(
            [sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.5
        )
        self.assertTrue(result.timed_out)
        self.assertLess(result.elapsed_ms, 5000)

    def test_missing_command_reports_error(self):
        runner = CommandRunner()
        result = runner.run(["vigil-definitely-not-a-command"], timeout=1)
        self.assertEqual(result.exit_code, -1)
        self.assertIn("命令启动失败", result.error)

    def test_string_command_rejected(self):
        runner = CommandRunner()
        with self.assertRaises(TypeError):
            runner.run("echo hi", timeout=1)


if __name__ == "__main__":
    unittest.main()
