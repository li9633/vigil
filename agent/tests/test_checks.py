"""Check 模型、Expectation 与执行器测试。"""

import unittest

from vigil_agent.checks.expect import evaluate, validate_expect
from vigil_agent.checks.model import (
    normalize_check,
    ordered_checks,
    validate_checks,
)
from vigil_agent.checks.runner import (
    STATUS_CRITICAL,
    STATUS_OK,
    STATUS_SKIPPED,
    STATUS_UNKNOWN,
    CheckRunner,
    aggregate_status,
)
from vigil_agent.config import default_config
from vigil_agent.probes.base import Observation, Probe, ProbeRegistry
from vigil_agent.probes.builtin import builtin_registry


class StaticProbe(Probe):
    id = "static"
    name = "静态"
    params_schema = {
        "value": {"type": "integer", "required": False, "default": 1},
    }
    default_expect = {"kind": "json", "path": "value", "equals": 1}
    default_timeout = 1.0

    def observe(self, ctx, params):
        return Observation({"value": params.get("value", 1)}, 3)


class ExplodingProbe(Probe):
    id = "boom"
    name = "异常"
    default_expect = {"kind": "json", "exists": True}
    default_timeout = 1.0

    def observe(self, ctx, params):
        raise RuntimeError("boom")


def _registry():
    return ProbeRegistry([StaticProbe(), ExplodingProbe()])


def _checks():
    return [
        {"id": "root", "probe": "static", "params": {"value": 1}},
        {
            "id": "child",
            "probe": "static",
            "params": {"value": 1},
            "depends_on": ["root"],
        },
    ]


class CheckModelTest(unittest.TestCase):
    def test_defaults_from_probe(self):
        check = normalize_check({"id": "a", "probe": "static"}, _registry())
        self.assertEqual(check["expect"], {"kind": "json", "path": "value", "equals": 1})
        self.assertEqual(check["timeout"], 1.0)
        self.assertEqual(check["params"], {"value": 1})

    def test_default_checks_pass_validation(self):
        errors = validate_checks(default_config()["checks"], builtin_registry())
        self.assertEqual(errors, [])

    def test_command_probe_not_allowed_for_server(self):
        registry = builtin_registry()
        checks = [
            {
                "id": "local-cmd",
                "probe": "command",
                "params": {"argv": ["true"]},
                "expect": {"kind": "command", "exit_code": 0},
            }
        ]
        errors = validate_checks(checks, registry, remote=True)
        self.assertTrue(any("remote" in item or "服务端" in item for item in errors))

    def test_unknown_dependency(self):
        checks = [
            {
                "id": "a",
                "probe": "static",
                "depends_on": ["missing"],
                "expect": {"kind": "json", "path": "value", "equals": 1},
            }
        ]
        errors = validate_checks(checks, _registry())
        self.assertTrue(any("depends_on" in item for item in errors))

    def test_cycle_detected(self):
        checks = [
            {
                "id": "a",
                "probe": "static",
                "depends_on": ["b"],
                "expect": {"kind": "json", "path": "value", "equals": 1},
            },
            {
                "id": "b",
                "probe": "static",
                "depends_on": ["a"],
                "expect": {"kind": "json", "path": "value", "equals": 1},
            },
        ]
        errors = validate_checks(checks, _registry())
        self.assertTrue(any("依赖" in item for item in errors))

    def test_missing_required_param(self):
        registry = builtin_registry()
        checks = [
            {
                "id": "web",
                "probe": "http",
                "params": {},
                "expect": {"kind": "http", "status": [200]},
            }
        ]
        errors = validate_checks(checks, registry)
        self.assertTrue(any("url" in item for item in errors))

    def test_ordered_checks_respects_dependencies(self):
        ordered = ordered_checks(
            [
                {"id": "b", "probe": "static", "depends_on": ["a"]},
                {"id": "a", "probe": "static"},
            ]
        )
        self.assertEqual([item["id"] for item in ordered], ["a", "b"])


class ExpectationTest(unittest.TestCase):
    def test_http_status_and_body(self):
        status, evidence = evaluate(
            {"kind": "http", "status": [204]},
            Observation({"status": 500, "body": ""}, 10),
        )
        self.assertEqual(status, STATUS_CRITICAL)
        self.assertEqual(evidence["reason"], "status_not_expected")

    def test_http_body_contains(self):
        status, _evidence = evaluate(
            {"kind": "http", "status": [200], "body_contains": "ok"},
            Observation({"status": 200, "body": "all ok"}, 10),
        )
        self.assertEqual(status, STATUS_OK)

    def test_http_network_failure_is_critical(self):
        status, evidence = evaluate(
            {"kind": "http", "status": [204]},
            Observation({"status": 0, "body": ""}, 10, "timeout"),
        )
        self.assertEqual(status, STATUS_CRITICAL)
        self.assertEqual(evidence["reason"], "request_failed")

    def test_http_latency_limit(self):
        status, evidence = evaluate(
            {"kind": "http", "status": [200], "max_latency_ms": 100},
            Observation({"status": 200, "body": ""}, 500),
        )
        self.assertEqual(status, STATUS_CRITICAL)
        self.assertEqual(evidence["reason"], "latency_exceeded")

    def test_dns_unresolved_is_critical(self):
        status, evidence = evaluate(
            {"kind": "dns", "resolved": True},
            Observation({"resolved": False, "addresses": []}, 5),
        )
        self.assertEqual(status, STATUS_CRITICAL)
        self.assertEqual(evidence["reason"], "dns_not_resolved")

    def test_probe_error_yields_unknown(self):
        status, _evidence = evaluate(
            {"kind": "carrier", "up": True}, Observation({}, 0, "read failed")
        )
        self.assertEqual(status, STATUS_UNKNOWN)

    def test_command_exit_code(self):
        status, _evidence = evaluate(
            {"kind": "command", "exit_code": 0},
            Observation({"exit_code": 2, "stdout": ""}, 5),
        )
        self.assertEqual(status, STATUS_CRITICAL)

    def test_validate_expect_unknown_kind(self):
        errors = []
        validate_expect({"kind": "nope"}, errors)
        self.assertTrue(any("kind" in item for item in errors))


class CheckRunnerTest(unittest.TestCase):
    def test_runs_in_dependency_order(self):
        registry = _registry()
        checks = [normalize_check(item, registry) for item in _checks()]
        results = CheckRunner(registry).run(checks)
        self.assertEqual(results["root"]["status"], STATUS_OK)
        self.assertEqual(results["child"]["status"], STATUS_OK)
        self.assertEqual(results["root"]["evidence"]["observed"], {"value": 1})

    def test_failed_dependency_skips_child(self):
        registry = _registry()
        checks = [
            normalize_check({"id": "root", "probe": "boom"}, registry),
            normalize_check(
                {"id": "child", "probe": "static", "depends_on": ["root"]}, registry
            ),
        ]
        results = CheckRunner(registry).run(checks)
        self.assertEqual(results["root"]["status"], STATUS_UNKNOWN)
        self.assertEqual(results["child"]["status"], STATUS_SKIPPED)
        self.assertEqual(results["child"]["evidence"]["blocked_by"], "root")

    def test_disabled_check_skipped(self):
        registry = _registry()
        checks = [
            normalize_check({"id": "a", "probe": "static", "enabled": False}, registry)
        ]
        results = CheckRunner(registry).run(checks)
        self.assertEqual(results["a"]["status"], STATUS_SKIPPED)
        self.assertEqual(results["a"]["evidence"]["reason"], "disabled")


class AggregateTest(unittest.TestCase):
    def _results(self, **statuses):
        return {
            key: {"status": value, "evidence": {}}
            for key, value in statuses.items()
        }

    def test_dependency_tree(self):
        results = self._results(a=STATUS_CRITICAL, b=STATUS_OK)
        self.assertEqual(
            aggregate_status(
                results, {"mode": "dependency_tree"}, ["a", "b"], []
            ),
            STATUS_CRITICAL,
        )

    def test_all_mode(self):
        results = self._results(a=STATUS_CRITICAL, b=STATUS_OK)
        self.assertEqual(aggregate_status(results, {"mode": "all"}, ["a", "b"]), STATUS_OK)

    def test_any_mode(self):
        results = self._results(a=STATUS_CRITICAL, b=STATUS_OK)
        self.assertEqual(
            aggregate_status(results, {"mode": "any"}, ["a", "b"]), STATUS_CRITICAL
        )

    def test_quorum_mode(self):
        results = self._results(
            a=STATUS_CRITICAL, b=STATUS_UNKNOWN, c=STATUS_OK
        )
        self.assertEqual(
            aggregate_status(results, {"mode": "quorum", "quorum": 2}, ["a", "b", "c"]),
            STATUS_CRITICAL,
        )
        self.assertEqual(
            aggregate_status(results, {"mode": "quorum", "quorum": 3}, ["a", "b", "c"]),
            STATUS_OK,
        )

    def test_all_skipped(self):
        results = self._results(a=STATUS_SKIPPED)
        self.assertEqual(
            aggregate_status(results, {"mode": "dependency_tree"}, ["a"], []),
            STATUS_SKIPPED,
        )


if __name__ == "__main__":
    unittest.main()
