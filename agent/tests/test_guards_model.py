"""Guard 配置模型测试（schema v3，check_refs）。"""

import copy
import unittest
import uuid

from vigil_agent.config import default_config
from vigil_agent.guards.model import (
    ALLOWED_ACTIONS,
    BUILTIN_NETWORK_ID,
    DEFAULT_POLICY,
    normalize_guard,
    validate_guards,
)


def _config():
    return default_config()


def _validate(guards, checks=None):
    errors = []
    payload = {"guards": guards}
    if checks is not None:
        payload["checks"] = checks
    validate_guards(payload, errors, checks=checks)
    return errors


class NormalizeGuardTest(unittest.TestCase):
    def test_fills_defaults_without_generating_checks(self):
        guard = normalize_guard({"name": "测试"})
        self.assertTrue(uuid.UUID(guard["id"]))
        self.assertEqual(guard["check_refs"], [])
        self.assertEqual(guard["policy"], DEFAULT_POLICY)
        self.assertEqual(guard["evaluate"], {"mode": "dependency_tree", "quorum": 1})
        self.assertEqual(guard["limits"], {"max_risk_level": "L1"})
        self.assertNotIn("checks", guard)

    def test_keeps_existing_fields(self):
        guard = normalize_guard(
            {
                "id": BUILTIN_NETWORK_ID,
                "name": "断网",
                "check_refs": ["wan-link"],
                "policy": {"interval": 60},
                "recovery": [{"action": "ifup_wan", "verify_after": 30}],
            }
        )
        self.assertEqual(guard["policy"]["interval"], 60)
        self.assertEqual(guard["policy"]["fail_threshold"], DEFAULT_POLICY["fail_threshold"])
        self.assertEqual(guard["recovery"][0]["verify_after"], 30)


class ValidateGuardsTest(unittest.TestCase):
    def setUp(self):
        self.cfg = _config()
        self.checks = self.cfg["checks"]

    def test_default_config_passes(self):
        self.assertEqual(validate_guards(self.cfg, [], checks=self.checks), [])

    def test_empty_guards_rejected(self):
        errors = _validate([], checks=self.checks)
        self.assertTrue(any("guards" in item for item in errors))

    def test_check_refs_must_exist(self):
        guard = normalize_guard({"name": "x", "check_refs": ["missing"]})
        errors = _validate([guard], checks=self.checks)
        self.assertTrue(any("check_refs" in item for item in errors))

    def test_check_refs_must_be_non_empty(self):
        guard = normalize_guard({"name": "x", "check_refs": []})
        errors = _validate([guard], checks=self.checks)
        self.assertTrue(any("check_refs" in item for item in errors))

    def test_duplicate_guard_id_rejected(self):
        first = normalize_guard({"name": "a", "check_refs": ["wan-link"]})
        second = copy.deepcopy(first)
        errors = _validate([first, second], checks=self.checks)
        self.assertTrue(any("id 重复" in item for item in errors))

    def test_duplicate_template_rejected(self):
        first = normalize_guard(
            {"name": "a", "template": "t", "check_refs": ["wan-link"]}
        )
        second = normalize_guard(
            {"name": "b", "template": "t", "check_refs": ["wan-link"]}
        )
        errors = _validate([first, second], checks=self.checks)
        self.assertTrue(any("template 重复" in item for item in errors))

    def test_invalid_uuid_rejected(self):
        guard = normalize_guard({"id": "not-a-uuid", "name": "x", "check_refs": ["wan-link"]})
        errors = _validate([guard], checks=self.checks)
        self.assertTrue(any("UUID" in item for item in errors))

    def test_policy_bounds(self):
        guard = normalize_guard(
            {"name": "x", "check_refs": ["wan-link"], "policy": {"interval": 1}}
        )
        errors = _validate([guard], checks=self.checks)
        self.assertTrue(any("policy.interval" in item for item in errors))

    def test_recovery_action_must_be_whitelisted(self):
        guard = normalize_guard(
            {
                "name": "x",
                "check_refs": ["wan-link"],
                "recovery": [{"action": "reboot"}],
            }
        )
        errors = _validate([guard], checks=self.checks)
        self.assertTrue(any("action" in item for item in errors))
        self.assertIn("ifup_wan", ALLOWED_ACTIONS)

    def test_restart_service_requires_target(self):
        guard = normalize_guard(
            {
                "name": "x",
                "check_refs": ["wan-link"],
                "recovery": [{"action": "restart_service"}],
            }
        )
        errors = _validate([guard], checks=self.checks)
        self.assertTrue(any("target" in item for item in errors))

    def test_evaluate_mode_rejected(self):
        guard = normalize_guard(
            {
                "name": "x",
                "check_refs": ["wan-link"],
                "evaluate": {"mode": "nope"},
            }
        )
        errors = _validate([guard], checks=self.checks)
        self.assertTrue(any("evaluate.mode" in item for item in errors))

    def test_action_safety_validation(self):
        cfg = _config()
        cfg["action_safety"]["max_risk_level"] = "L3"
        errors = []
        validate_guards(cfg, errors, checks=self.checks)
        self.assertTrue(any("max_risk_level" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
