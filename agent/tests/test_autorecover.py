"""恢复链执行测试。"""

import unittest

from vigil_agent.guards.fsm import STATE_FAILED, STATE_OK, GuardStateMachine
from vigil_agent.guards.recovery import RecoveryRunner


class FakeExecutor:
    def __init__(self, outcomes=None):
        self.calls = []
        self.outcomes = outcomes or {}

    def execute(self, name, target=None):
        self.calls.append((name, target))
        executed = self.outcomes.get(name, True)
        return {
            "action": name,
            "target": target or "",
            "allowed": executed,
            "executed": executed,
            "result": "success" if executed else "rejected",
            "reason": "" if executed else "cooldown",
        }


def _guard(steps):
    return {
        "id": "22222222-2222-4222-8222-222222222222",
        "name": "测试守护",
        "policy": {"gave_up_after": 3, "success_threshold": 1},
        "recovery": steps,
    }


class RecoveryRunnerTest(unittest.TestCase):
    def setUp(self):
        self.fsm = GuardStateMachine(persist=False)

    def test_stops_after_first_success(self):
        guard = _guard(
            [
                {"action": "ifup_wan"},
                {"action": "restart_service", "target": "dnsmasq"},
            ]
        )
        executor = FakeExecutor()
        runner = RecoveryRunner(
            executor, self.fsm, verify=lambda: True, sleep=lambda seconds: None
        )
        outcome = runner.run(guard)
        self.assertTrue(outcome["recovered"])
        self.assertEqual(executor.calls, [("ifup_wan", None)])
        self.assertEqual(self.fsm.snapshot(guard["id"])["status"], STATE_OK)

    def test_continue_on_failure_moves_to_next_step(self):
        guard = _guard(
            [
                {"action": "ifup_wan", "continue_on_failure": True},
                {"action": "restart_service", "target": "dnsmasq"},
            ]
        )
        executor = FakeExecutor()
        answers = iter([False, True])
        runner = RecoveryRunner(
            executor, self.fsm, verify=lambda: next(answers), sleep=lambda seconds: None
        )
        outcome = runner.run(guard)
        self.assertTrue(outcome["recovered"])
        self.assertEqual(
            executor.calls,
            [("ifup_wan", None), ("restart_service", "dnsmasq")],
        )

    def test_stops_when_verify_fails_and_continue_is_false(self):
        guard = _guard(
            [
                {"action": "ifup_wan", "continue_on_failure": False},
                {"action": "restart_service", "target": "dnsmasq"},
            ]
        )
        executor = FakeExecutor()
        runner = RecoveryRunner(
            executor, self.fsm, verify=lambda: False, sleep=lambda seconds: None
        )
        outcome = runner.run(guard)
        self.assertFalse(outcome["recovered"])
        self.assertEqual(executor.calls, [("ifup_wan", None)])
        state = self.fsm.snapshot(guard["id"])
        self.assertEqual(state["status"], STATE_FAILED)
        self.assertEqual(state["recovery_rounds"], 1)

    def test_rejected_action_does_not_count_as_failed_round(self):
        guard = _guard([{"action": "ifup_wan"}])
        executor = FakeExecutor(outcomes={"ifup_wan": False})
        runner = RecoveryRunner(
            executor, self.fsm, verify=lambda: False, sleep=lambda seconds: None
        )
        outcome = runner.run(guard)
        self.assertTrue(outcome["skipped"])
        state = self.fsm.snapshot(guard["id"])
        self.assertEqual(state["status"], STATE_FAILED)
        self.assertEqual(state["recovery_rounds"], 0)

    def test_gives_up_after_configured_rounds(self):
        guard = _guard([{"action": "ifup_wan"}])
        guard["policy"]["gave_up_after"] = 2
        executor = FakeExecutor()
        runner = RecoveryRunner(
            executor, self.fsm, verify=lambda: False, sleep=lambda seconds: None
        )
        runner.run(guard)
        self.assertEqual(self.fsm.snapshot(guard["id"])["status"], STATE_FAILED)
        outcome = runner.run(guard)
        self.assertFalse(outcome["recovered"])
        self.assertEqual(self.fsm.snapshot(guard["id"])["status"], "GAVE_UP")

    def test_action_callback_receives_results(self):
        guard = _guard([{"action": "ifup_wan"}])
        seen = []
        runner = RecoveryRunner(
            FakeExecutor(),
            self.fsm,
            verify=lambda: True,
            sleep=lambda seconds: None,
            on_action=lambda g, result: seen.append((g["id"], result["action"])),
        )
        runner.run(guard)
        self.assertEqual(seen, [(guard["id"], "ifup_wan")])


if __name__ == "__main__":
    unittest.main()
