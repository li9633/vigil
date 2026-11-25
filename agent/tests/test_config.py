"""配置加载与校验测试。"""

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
        self.assertEqual(cfg["schema_version"], 1)
        self.assertEqual(cfg["server"]["base_url"], "")
        self.assertEqual(cfg["server"]["heartbeat_interval"], 30)
        self.assertTrue(cfg["server"]["verify_tls"])
        self.assertEqual(cfg["detect"]["wan_device"], "auto")
        self.assertEqual(cfg["detect"]["wan_interface"], "wan")
        self.assertEqual(cfg["detect"]["dns_probe"], "www.baidu.com")
        self.assertEqual(cfg["detect"]["timeout"], 3)
        self.assertEqual(
            cfg["detect"]["http_probes"],
            ["http://connect.rom.miui.com/generate_204"],
        )
        self.assertTrue(cfg["action"]["enabled"])
        self.assertEqual(cfg["action"]["max_level"], "L1")
        self.assertEqual(cfg["action"]["cooldown_seconds"], 300)
        self.assertEqual(cfg["action"]["max_per_hour"], 3)
        self.assertEqual(cfg["httpd"]["port"], 8787)

    def test_missing_file_uses_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "missing.json")
            cfg = load_config(path, env={})
        self.assertEqual(cfg, default_config())

    def test_file_overrides_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "server": {"client_id": "cl_1", "base_url": "https://vigil.example.com"},
                        "detect": {"wan_device": "pppoe-wan"},
                    },
                    handle,
                )
            cfg = load_config(path, env={})
        self.assertEqual(cfg["server"]["client_id"], "cl_1")
        self.assertEqual(cfg["server"]["base_url"], "https://vigil.example.com")
        self.assertEqual(cfg["detect"]["wan_device"], "pppoe-wan")
        # 未覆盖的键保持默认
        self.assertEqual(cfg["detect"]["timeout"], 3)
        self.assertEqual(cfg["action"]["max_per_hour"], 3)

    def test_environment_overrides(self):
        env = {
            "VIGIL_SERVER_BASE_URL": "https://vigil.example.com",
            "VIGIL_SERVER_CLIENT_ID": "cl_env",
            "VIGIL_SERVER_SECRET": "secret value",
            "VIGIL_SERVER_HEARTBEAT_INTERVAL": "45",
            "VIGIL_SERVER_VERIFY_TLS": "false",
            "VIGIL_WAN_DEVICE": "eth0",
            "VIGIL_HTTP_PROBES": "http://a.example/204, http://b.example/204",
            "VIGIL_DETECT_TIMEOUT": "5",
            "VIGIL_ACTION_ENABLED": "0",
            "VIGIL_ACTION_COOLDOWN_SECONDS": "60",
            "VIGIL_HTTPD_PORT": "9000",
            "VIGIL_HTTPD_TOKEN": "tok",
        }
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_config(os.path.join(tmp, "none.json"), env=env)
        self.assertEqual(cfg["server"]["base_url"], "https://vigil.example.com")
        self.assertEqual(cfg["server"]["client_id"], "cl_env")
        self.assertEqual(cfg["server"]["secret"], "secret value")
        self.assertEqual(cfg["server"]["heartbeat_interval"], 45)
        self.assertFalse(cfg["server"]["verify_tls"])
        self.assertEqual(cfg["detect"]["wan_device"], "eth0")
        self.assertEqual(
            cfg["detect"]["http_probes"],
            ["http://a.example/204", "http://b.example/204"],
        )
        self.assertEqual(cfg["detect"]["timeout"], 5.0)
        self.assertFalse(cfg["action"]["enabled"])
        self.assertEqual(cfg["action"]["cooldown_seconds"], 60)
        self.assertEqual(cfg["httpd"]["port"], 9000)
        self.assertEqual(cfg["httpd"]["token"], "tok")

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

    def test_bad_timeout(self):
        message = self._invalid(lambda cfg: cfg["detect"].update({"timeout": 0}))
        self.assertIn("detect.timeout", message)

    def test_bad_probes(self):
        message = self._invalid(
            lambda cfg: cfg["detect"].update({"http_probes": ["not-a-url"]})
        )
        self.assertIn("http_probes", message)

    def test_bad_max_level(self):
        message = self._invalid(lambda cfg: cfg["action"].update({"max_level": "L3"}))
        self.assertIn("max_level", message)

    def test_bad_port(self):
        message = self._invalid(lambda cfg: cfg["httpd"].update({"port": 70000}))
        self.assertIn("httpd.port", message)

    def test_multiple_errors_reported(self):
        message = self._invalid(
            lambda cfg: (
                cfg["action"].update({"max_per_hour": 0}),
                cfg["detect"].update({"dns_probe": ""}),
            )
        )
        self.assertIn("max_per_hour", message)
        self.assertIn("dns_probe", message)

    def test_get_in(self):
        cfg = default_config()
        self.assertEqual(get_in(cfg, "detect.timeout"), 3)
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


if __name__ == "__main__":
    unittest.main()
