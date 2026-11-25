"""Probe / Check 执行器测试。"""

import os
import tempfile
import unittest

from vigil_agent.config import default_config
from vigil_agent.guards.checks import aggregate_status, run_checks


def _write_carrier(root, device, value):
    directory = os.path.join(root, device)
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "carrier"), "w", encoding="utf-8") as handle:
        handle.write(value)


class RunChecksTest(unittest.TestCase):
    def test_carrier_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_carrier(tmp, "eth0", "1")
            guard = {
                "checks": [
                    {"probe": "carrier", "params": {"device": "eth0"}},
                ]
            }
            results = run_checks(guard, default_config(), sys_class_net=tmp)
        self.assertEqual(results["carrier"]["status"], "ok")
        self.assertEqual(results["carrier"]["evidence"]["device"], "eth0")

    def test_carrier_down_skips_dependent_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_carrier(tmp, "eth0", "0")
            guard = {
                "checks": [
                    {"probe": "carrier", "params": {"device": "eth0"}},
                    {"probe": "pppoe", "depends_on": ["carrier"]},
                ]
            }
            results = run_checks(guard, default_config(), sys_class_net=tmp)
        self.assertEqual(results["carrier"]["status"], "critical")
        self.assertEqual(results["pppoe"]["status"], "skipped")
        self.assertEqual(results["pppoe"]["evidence"]["blocked_by"], "carrier")

    def test_unknown_probe_reports_unknown(self):
        guard = {"checks": [{"probe": "no.such.probe"}]}
        results = run_checks(guard, default_config())
        self.assertEqual(results["no.such.probe"]["status"], "unknown")

    def test_http_params_are_used(self):
        guard = {
            "checks": [
                {
                    "probe": "http",
                    "params": {"url": ["http://example.com/generate_204"]},
                }
            ]
        }
        seen = []

        def fake_get(url, timeout):
            seen.append(url)
            return 204, 5, ""

        results = run_checks(guard, default_config(), http_get=fake_get)
        self.assertEqual(seen, ["http://example.com/generate_204"])
        self.assertEqual(results["http"]["status"], "ok")

    def test_check_level_timeout_overrides_default(self):
        guard = {
            "checks": [
                {"probe": "http", "params": {"url": ["http://a"]}, "timeout": 7}
            ]
        }
        seen = []

        def fake_get(url, timeout):
            seen.append(timeout)
            return 204, 1, ""

        run_checks(guard, default_config(), http_get=fake_get)
        self.assertEqual(seen, [7.0])

    def test_custom_check_id_is_used(self):
        guard = {
            "checks": [
                {
                    "id": "wan-http",
                    "probe": "http",
                    "params": {"url": ["http://a"]},
                }
            ]
        }
        results = run_checks(guard, default_config(), http_get=lambda u, t: (204, 1, ""))
        self.assertIn("wan-http", results)
        self.assertEqual(results["wan-http"]["probe"], "http")


class AggregateTest(unittest.TestCase):
    def _results(self, *statuses):
        return {
            "c%d" % index: {"status": status}
            for index, status in enumerate(statuses)
        }

    def test_dependency_tree_takes_worst(self):
        results = self._results("ok", "skipped", "warn")
        self.assertEqual(aggregate_status(results), "warn")

    def test_all_mode(self):
        self.assertEqual(
            aggregate_status(self._results("critical", "critical"), {"mode": "all"}),
            "critical",
        )
        self.assertEqual(
            aggregate_status(self._results("critical", "ok"), {"mode": "all"}),
            "ok",
        )

    def test_quorum_mode(self):
        evaluate = {"mode": "quorum", "quorum": 2}
        self.assertEqual(
            aggregate_status(self._results("critical", "critical", "ok"), evaluate),
            "critical",
        )
        self.assertEqual(
            aggregate_status(self._results("critical", "ok"), evaluate),
            "warn",
        )

    def test_any_mode(self):
        self.assertEqual(
            aggregate_status(self._results("ok", "critical"), {"mode": "any"}),
            "critical",
        )

    def test_all_skipped(self):
        self.assertEqual(aggregate_status(self._results("skipped")), "skipped")


if __name__ == "__main__":
    unittest.main()
