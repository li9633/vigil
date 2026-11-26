"""配置加载与校验测试（schema v3）。"""

import json
import os
import tempfile
import unittest

from vigil_agent.config import (
    ConfigError,
    default_config,
    get_in,
    load_config,
    save_config,
    validate_config,
)


class ConfigDefaultsTest(unittest.TestCase):
    def test_defaults_match_design(self):
        cfg = default_config()
        self.assertEqual(cfg["schema_version"], 3)
        self.assertEqual(cfg["server"]["heartbeat_interval"], 30)
        self.assertTrue(cfg["server"]["verify_tls"])
        self.assertNotIn("detect", cfg)
        self.assertEqual([item["id"] for item in cfg["checks"]], [
            "wan-link",
            "pppoe-session",
            "default-route",
            "dns",
            "http204",
        ])
        self.assertEqual(
            cfg["guards"][0]["check_refs"],
            ["wan-link", "pppoe-session", "default-route", "dns", "http204"],
        )
        self.assertEqual(cfg["action_safety"]["max_risk_level"], "L1")
        self.assertEqual(cfg["action"]["wan_interface"], "wan")
        self.assertEqual(cfg["httpd"]["port"], 8787)

    def test_default_config_is_valid(self):
        validate_config(default_config())

    def test_missing_file_uses_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "missing.json")
            cfg = load_config(path, env={})
        self.assertEqual(cfg["schema_version"], 3)
        self.assertEqual(len(cfg["checks"]), 5)

    def test_file_overrides_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "server": {
                            "client_id": "cl_1",
                            "base_url": "https://vigil.example.com",
                        },
                        "action": {"wan_interface": "pppoe-wan"},
                    },
                    handle,
                )
            cfg = load_config(path, env={})
        self.assertEqual(cfg["server"]["client_id"], "cl_1")
        self.assertEqual(cfg["server"]["base_url"], "https://vigil.example.com")
        self.assertEqual(cfg["action"]["wan_interface"], "pppoe-wan")
        self.assertEqual(cfg["httpd"]["port"], 8787)

    def test_environment_overrides(self):
        env = {
            "VIGIL_SERVER_BASE_URL": "https://vigil.example.com",
            "VIGIL_SERVER_CLIENT_ID": "cl_env",
            "VIGIL_SERVER_SECRET": "secret value",
            "VIGIL_SERVER_HEARTBEAT_INTERVAL": "45",
            "VIGIL_SERVER_VERIFY_TLS": "false",
            "VIGIL_WAN_INTERFACE": "eth0",
            "VIGIL_ACTION_ENABLED": "0",
            "VIGIL_ACTION_COOLDOWN_SECONDS": "60",
            "VIGIL_ACTION_ALLOWED_SERVICES": "dnsmasq, dropbear",
            "VIGIL_HTTPD_PORT": "9000",
            "VIGIL_HTTPD_TOKEN": "tok",
        }
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_config(os.path.join(tmp, "none.json"), env=env)
        self.assertEqual(cfg["server"]["base_url"], "https://vigil.example.com")
        self.assertEqual(cfg["server"]["secret"], "secret value")
        self.assertEqual(cfg["server"]["heartbeat_interval"], 45)
        self.assertFalse(cfg["server"]["verify_tls"])
        self.assertEqual(cfg["action"]["wan_interface"], "eth0")
        self.assertFalse(cfg["action_safety"]["global_enabled"])
        self.assertEqual(cfg["action"]["cooldown_seconds"], 60)
        self.assertEqual(cfg["action"]["allowed_services"], ["dnsmasq", "dropbear"])
        self.assertEqual(cfg["httpd"]["port"], 9000)

    def test_env_config_path_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "from-env.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"agent": {"name": "router-a"}}, handle)
            cfg = load_config(env={"VIGIL_CONFIG": path})
        self.assertEqual(cfg["agent"]["name"], "router-a")

    def test_invalid_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{not json")
            with self.assertRaises(ConfigError):
                load_config(path, env={})

    def test_non_object_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "list.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("[]")
            with self.assertRaises(ConfigError):
                load_config(path, env={})

    def test_bad_env_bool_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError):
                load_config(
                    os.path.join(tmp, "none.json"),
                    env={"VIGIL_ACTION_ENABLED": "maybe"},
                )

    def test_bad_env_int_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError):
                load_config(
                    os.path.join(tmp, "none.json"),
                    env={"VIGIL_HTTPD_PORT": "not-a-number"},
                )


class ConfigValidationTest(unittest.TestCase):
    def _invalid(self, mutate):
        cfg = default_config()
        mutate(cfg)
        with self.assertRaises(ConfigError) as ctx:
            validate_config(cfg)
        return str(ctx.exception)

    def test_schema_2_is_rejected(self):
        message = self._invalid(lambda cfg: cfg.update({"schema_version": 2}))
        self.assertIn("schema_version", message)

    def test_bad_base_url(self):
        message = self._invalid(
            lambda cfg: cfg["server"].update({"base_url": "ftp://example.com"})
        )
        self.assertIn("base_url", message)

    def test_bad_heartbeat_interval(self):
        message = self._invalid(
            lambda cfg: cfg["server"].update({"heartbeat_interval": 0})
        )
        self.assertIn("heartbeat_interval", message)

    def test_bad_check_timeout(self):
        message = self._invalid(lambda cfg: cfg["checks"][0].update({"timeout": 0}))
        self.assertIn("timeout", message)

    def test_unknown_probe_is_rejected(self):
        message = self._invalid(lambda cfg: cfg["checks"][0].update({"probe": "nope"}))
        self.assertIn("probe", message)

    def test_unknown_dependency_is_rejected(self):
        message = self._invalid(
            lambda cfg: cfg["checks"][0].update({"depends_on": ["missing"]})
        )
        self.assertIn("depends_on", message)

    def test_check_cycle_is_rejected(self):
        def mutate(cfg):
            cfg["checks"][0]["depends_on"] = ["http204"]

        message = self._invalid(mutate)
        self.assertIn("依赖", message)

    def test_guard_unknown_check_ref_is_rejected(self):
        message = self._invalid(
            lambda cfg: cfg["guards"][0]["check_refs"].append("missing")
        )
        self.assertIn("check_refs", message)

    def test_guard_empty_check_refs_is_rejected(self):
        message = self._invalid(lambda cfg: cfg["guards"][0].update({"check_refs": []}))
        self.assertIn("check_refs", message)

    def test_bad_action_max_level(self):
        message = self._invalid(
            lambda cfg: cfg["action_safety"].update({"max_risk_level": "L3"})
        )
        self.assertIn("max_risk_level", message)

    def test_bad_port(self):
        message = self._invalid(lambda cfg: cfg["httpd"].update({"port": 70000}))
        self.assertIn("httpd.port", message)

    def test_multiple_errors_reported(self):
        message = self._invalid(
            lambda cfg: (
                cfg["action_safety"].update({"max_actions_per_hour": 0}),
                cfg["checks"][0].update({"timeout": 0}),
            )
        )
        self.assertIn("max_actions_per_hour", message)
        self.assertIn("timeout", message)

    def test_get_in(self):
        cfg = default_config()
        self.assertEqual(get_in(cfg, "action.wan_interface"), "wan")
        self.assertEqual(get_in(cfg, "missing.key", "fallback"), "fallback")


class ConfigSaveTest(unittest.TestCase):
    def test_save_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nested", "config.json")
            cfg = default_config()
            cfg["server"]["base_url"] = "https://vigil.example.com"
            cfg["server"]["client_id"] = "cl_saved"
            cfg["server"]["secret"] = "shh"
            save_config(cfg, path)
            self.assertTrue(os.path.isfile(path))
            loaded = load_config(path, env={})
        self.assertEqual(loaded["server"]["base_url"], "https://vigil.example.com")
        self.assertEqual(loaded["server"]["client_id"], "cl_saved")
        self.assertEqual(loaded["server"]["secret"], "shh")
        self.assertEqual(loaded["httpd"]["port"], 8787)

    def test_legacy_file_is_rejected_without_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "schema_version": 2,
                        "server": {"client_id": "cl_legacy"},
                        "guards": [{"checks": [{"probe": "carrier"}]}],
                    },
                    handle,
                )
            with self.assertRaises(ConfigError):
                load_config(path, env={})
            with open(path, encoding="utf-8") as handle:
                persisted = json.load(handle)
        self.assertEqual(persisted["schema_version"], 2)

    def test_env_overrides_are_not_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"server": {"client_id": "cl_legacy"}}, handle)
            cfg = load_config(path, env={"VIGIL_SERVER_SECRET": "env-only-secret"})
            with open(path, encoding="utf-8") as handle:
                persisted = json.load(handle)
        self.assertEqual(cfg["server"]["secret"], "env-only-secret")
        self.assertEqual(persisted, {"server": {"client_id": "cl_legacy"}})

    def test_invalid_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            raw = {"schema_version": 2, "server": {"base_url": "ftp://bad"}}
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(raw, handle)
            with self.assertRaises(ConfigError):
                load_config(
                    path, env={"VIGIL_SERVER_BASE_URL": "https://vigil.example.com"}
                )
            with open(path, encoding="utf-8") as handle:
                persisted = json.load(handle)
        self.assertEqual(persisted, raw)


if __name__ == "__main__":
    unittest.main()
