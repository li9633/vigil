"""动作白名单、冷却、每小时上限与复检逻辑测试。"""

import os
import tempfile
import unittest

from vigil_agent.action.executor import ActionExecutor
from vigil_agent.config import default_config
from vigil_agent.utils.proc import ProcessResult


class FakeRunner:
    def __init__(self, exit_code=0, stdout="", stderr="", timed_out=False):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out
        self.calls = []

    def run(self, args, timeout=None, env=None, cwd=None):
        self.calls.append(" ".join(args))
        return ProcessResult(
            exit_code=self.exit_code,
            stdout=self.stdout,
            stderr=self.stderr,
            timed_out=self.timed_out,
            latency_ms=5,
        )


class ExecutorTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = os.path.join(self.tmp.name, "state.json")
        self.lock_path = os.path.join(self.tmp.name, "action.lock")
        self.clock = [1000.0]
        self.sleeps = []
        self.runner = FakeRunner()

    def tearDown(self):
        self.tmp.cleanup()

    def make_executor(self, runner=None, **overrides):
        cfg = default_config()
        for key, value in overrides.items():
            if key == "enabled":
                cfg["action_safety"]["global_enabled"] = value
            elif key == "max_level":
                cfg["action_safety"]["max_risk_level"] = value
            elif key == "max_per_hour":
                cfg["action_safety"]["max_actions_per_hour"] = value
            else:
                cfg["action"][key] = value
        return ActionExecutor(
            cfg,
            runner=runner or self.runner,
            state_path=self.state_path,
            lock_path=self.lock_path,
            now=lambda: self.clock[0],
            sleep=self.sleeps.append,
            use_lock=False,
        )


class WhitelistTest(ExecutorTestBase):
    def test_unknown_action_rejected(self):
        executor = self.make_executor()
        result = executor.execute("rm_rf")
        self.assertFalse(result["allowed"])
        self.assertFalse(result["executed"])
        self.assertEqual(result["reason"], "not_whitelisted")
        self.assertEqual(self.runner.calls, [])

    def test_restart_service_requires_target(self):
        executor = self.make_executor()
        result = executor.execute("restart_service")
        self.assertEqual(result["reason"], "target_required")

    def test_restart_service_rejects_service_outside_whitelist(self):
        executor = self.make_executor()
        result = executor.execute("restart_service", "network")
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "target_not_whitelisted")

    def test_restart_service_rejects_injection(self):
        executor = self.make_executor()
        result = executor.execute("restart_service", "dnsmasq; reboot")
        self.assertEqual(result["reason"], "target_invalid")

    def test_ifup_wan_runs_whitelisted_command(self):
        executor = self.make_executor()
        result = executor.execute("ifup_wan")
        self.assertTrue(result["allowed"])
        self.assertTrue(result["executed"])
        self.assertEqual(result["result"], "success")
        self.assertEqual(self.runner.calls, ["ifup wan"])
        self.assertTrue(os.path.isfile(self.state_path))

    def test_restart_service_runs_whitelisted_service(self):
        executor = self.make_executor()
        result = executor.execute("restart_service", "dnsmasq")
        self.assertEqual(result["result"], "success")
        self.assertEqual(self.runner.calls, ["/etc/init.d/dnsmasq restart"])

    def test_disabled_rejects_all(self):
        executor = self.make_executor(enabled=False)
        result = executor.execute("ifup_wan")
        self.assertEqual(result["reason"], "disabled")

    def test_l0_level_rejects_l1_action(self):
        executor = self.make_executor(max_level="L0")
        result = executor.execute("ifup_wan")
        self.assertEqual(result["reason"], "level_disabled")


class CooldownTest(ExecutorTestBase):
    def test_cooldown_blocks_and_persists(self):
        first = self.make_executor()
        self.assertEqual(first.execute("ifup_wan")["result"], "success")

        self.clock[0] += 10
        second = self.make_executor()
        result = second.execute("ifup_wan")
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "cooldown")
        self.assertEqual(result["cooldown_remaining"], 290)
        self.assertEqual(len(self.runner.calls), 1)

    def test_cooldown_expires(self):
        executor = self.make_executor()
        executor.execute("ifup_wan")
        self.clock[0] += 301
        result = executor.execute("ifup_wan")
        self.assertTrue(result["allowed"])
        self.assertEqual(len(self.runner.calls), 2)

    def test_hourly_limit(self):
        executor = self.make_executor(cooldown_seconds=0, max_per_hour=2)
        first = executor.execute("ifup_wan")
        self.clock[0] += 10
        second = executor.execute("ifup_wan")
        self.clock[0] += 10
        third = executor.execute("ifup_wan")
        self.assertTrue(first["executed"])
        self.assertTrue(second["executed"])
        self.assertFalse(third["allowed"])
        self.assertEqual(third["reason"], "hourly_limit")
        self.assertEqual(len(self.runner.calls), 2)

    def test_maintenance_window_blocks_actions(self):
        executor = self.make_executor()
        executor.set_maintenance(60)
        result = executor.execute("ifup_wan")
        self.assertEqual(result["reason"], "maintenance")
        self.assertEqual(result["cooldown_remaining"], 60)

        self.clock[0] += 61
        self.assertTrue(executor.execute("ifup_wan")["allowed"])

    def test_failed_command_is_recorded(self):
        runner = FakeRunner(exit_code=1, stderr="ifup: not found")
        executor = self.make_executor(runner=runner)
        result = executor.execute("ifup_wan")
        self.assertTrue(result["executed"])
        self.assertEqual(result["result"], "failed")
        self.assertEqual(result["exit_code"], 1)
        self.assertIn("not found", result["output"])
        stats = executor.stats()
        self.assertEqual(stats["actions"]["ifup_wan"]["last_result"], "failed")

    def test_stats_reports_cooldown(self):
        executor = self.make_executor()
        executor.execute("ifup_wan")
        self.clock[0] += 100
        stats = executor.stats()
        self.assertEqual(stats["actions"]["ifup_wan"]["cooldown_remaining"], 200)


class RecheckTest(ExecutorTestBase):
    def test_recheck_success_stops_after_one_attempt(self):
        executor = self.make_executor()
        outcome = executor.execute_with_recheck("ifup_wan", lambda: True)
        self.assertTrue(outcome["recovered"])
        self.assertEqual(len(outcome["attempts"]), 1)
        self.assertEqual(self.sleeps, [20])
        self.assertEqual(len(self.runner.calls), 1)

    def test_recheck_failure_retries_once(self):
        executor = self.make_executor()
        outcome = executor.execute_with_recheck("ifup_wan", lambda: False)
        self.assertFalse(outcome["recovered"])
        self.assertEqual(len(outcome["attempts"]), 2)
        self.assertEqual(self.sleeps, [20, 120, 20])
        self.assertEqual(len(self.runner.calls), 2)

    def test_rejected_action_skips_recheck(self):
        executor = self.make_executor(enabled=False)
        outcome = executor.execute_with_recheck("ifup_wan", lambda: True)
        self.assertFalse(outcome["recovered"])
        self.assertEqual(len(outcome["attempts"]), 1)
        self.assertEqual(self.sleeps, [])


if __name__ == "__main__":
    unittest.main()
