"""Guard 配置模型与迁移测试。"""

import copy
import unittest
import uuid

from vigil_agent.config import ConfigError, default_config, validate_config
from vigil_agent.guards.model import (
    BUILTIN_NETWORK_ID,
    BUILTIN_NETWORK_TEMPLATE,
    build_network_guard,
    migrate_config,
)


class GuardMigrationTest(unittest.TestCase):
    def test_schema1_migrates_to_network_guard(self):
        cfg = default_config()
        cfg["schema_version"] = 1
        cfg["detect"].update({"wan_device": "eth0", "dns_probe": "example.com"})
        cfg["action"].update(
            {"enabled": False, "failure_threshold": 4, "cooldown_seconds": 120}
        )

        migrate_config(cfg)

        self.assertEqual(cfg["schema_version"], 2)
        self.assertEqual(len(cfg["guards"]), 1)
        guard = cfg["guards"][0]
        self.assertEqual(guard["id"], BUILTIN_NETWORK_ID)
        self.assertEqual(guard["template"], BUILTIN_NETWORK_TEMPLATE)
        self.assertEqual(guard["policy"]["fail_threshold"], 4)
        self.assertEqual(guard["policy"]["cooldown"], 120)
        self.assertFalse(cfg["action_safety"]["global_enabled"])

        probes = [check["probe"] for check in guard["checks"]]
        self.assertEqual(probes, ["carrier", "pppoe", "route", "dns", "http"])
        self.assertEqual(guard["checks"][0]["params"]["device"], "eth0")
        self.assertEqual(guard["checks"][3]["params"]["name"], "example.com")

    def test_migration_is_idempotent(self):
        cfg = migrate_config(default_config())
        first = copy.deepcopy(cfg)
        migrate_config(cfg)
        self.assertEqual(cfg, first)

    def test_existing_guard_is_preserved(self):
        cfg = default_config()
        cfg["schema_version"] = 2
        guard = build_network_guard(cfg)
        guard["id"] = str(uuid.uuid4())
        guard["name"] = "自定义网络守护"
        cfg["guards"] = [guard]
        cfg["action_safety"] = {
            "global_enabled": True,
            "max_risk_level": "L1",
            "max_actions_per_hour": 2,
            "maintenance_windows": [],
        }

        migrate_config(cfg)

        self.assertEqual(cfg["guards"][0]["id"], guard["id"])
        self.assertEqual(cfg["guards"][0]["name"], "自定义网络守护")
        self.assertEqual(cfg["action_safety"]["max_actions_per_hour"], 2)

    def test_missing_optional_fields_are_normalized(self):
        cfg = default_config()
        cfg["guards"] = [
            {
                "id": str(uuid.uuid4()),
                "name": "最小守护",
                "checks": [{"probe": "route"}],
                "recovery": [{"action": "ifup_wan"}],
            }
        ]
        migrate_config(cfg)
        guard = cfg["guards"][0]
        self.assertEqual(guard["policy"]["fail_threshold"], 2)
        self.assertEqual(guard["policy"]["success_threshold"], 1)
        self.assertEqual(guard["limits"]["max_risk_level"], "L1")
        self.assertIsInstance(guard["enabled"], bool)


class GuardValidationTest(unittest.TestCase):
    def test_duplicate_uuid_is_rejected(self):
        cfg = migrate_config(default_config())
        cfg["guards"].append(copy.deepcopy(cfg["guards"][0]))
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_duplicate_template_is_rejected(self):
        cfg = migrate_config(default_config())
        second = copy.deepcopy(cfg["guards"][0])
        second["id"] = str(uuid.uuid4())
        cfg["guards"].append(second)
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_unknown_action_is_rejected(self):
        cfg = migrate_config(default_config())
        cfg["guards"][0]["recovery"] = [{"action": "reboot"}]
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_restart_service_requires_whitelisted_target(self):
        cfg = migrate_config(default_config())
        cfg["guards"][0]["recovery"] = [{"action": "restart_service"}]
        with self.assertRaises(ConfigError):
            validate_config(cfg)

        cfg["guards"][0]["recovery"] = [
            {"action": "restart_service", "target": "dnsmasq"}
        ]
        validate_config(cfg)

    def test_invalid_policy_is_rejected(self):
        cfg = migrate_config(default_config())
        cfg["guards"][0]["policy"]["fail_threshold"] = 0
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_risk_level_is_limited(self):
        cfg = migrate_config(default_config())
        cfg["guards"][0]["limits"]["max_risk_level"] = "L2"
        with self.assertRaises(ConfigError):
            validate_config(cfg)


if __name__ == "__main__":
    unittest.main()
