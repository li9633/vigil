"""Guard 状态机测试。"""

import os
import tempfile
import unittest

from vigil_agent.guards.fsm import (
    STATE_FAILED,
    STATE_GAVE_UP,
    STATE_OK,
    STATE_PENDING,
    STATE_RECOVERING,
    GuardStateMachine,
)


def _guard(fail_threshold=2, mode="consecutive", window=90, success=1, gave_up=3):
    return {
        "id": "11111111-1111-4111-8111-111111111111",
        "policy": {
            "fail_threshold": fail_threshold,
            "fail_mode": mode,
            "fail_window": window,
            "success_threshold": success,
            "gave_up_after": gave_up,
        },
    }


class GuardStateMachineTest(unittest.TestCase):
    def setUp(self):
        self.fsm = GuardStateMachine(persist=False)

    def test_consecutive_threshold(self):
        guard = _guard()
        state = self.fsm.process(guard, "critical", now=100)
        self.assertEqual(state["status"], STATE_PENDING)
        self.assertFalse(self.fsm.should_recover(guard["id"], now=100))

        state = self.fsm.process(guard, "critical", now=130)
        self.assertEqual(state["status"], STATE_FAILED)
        self.assertTrue(self.fsm.should_recover(guard["id"], now=130))

    def test_ok_resets_counters(self):
        guard = _guard()
        self.fsm.process(guard, "critical", now=100)
        state = self.fsm.process(guard, "ok", now=130)
        self.assertEqual(state["status"], STATE_OK)
        self.assertEqual(state["failure_count"], 0)
        self.assertFalse(self.fsm.should_recover(guard["id"], now=130))

    def test_window_mode_ignores_old_failures(self):
        guard = _guard(mode="window", window=60)
        self.fsm.process(guard, "critical", now=0)
        state = self.fsm.process(guard, "critical", now=100)
        self.assertEqual(state["status"], STATE_PENDING)

        state = self.fsm.process(guard, "critical", now=120)
        self.assertEqual(state["status"], STATE_FAILED)

    def test_skipped_does_not_change_state(self):
        guard = _guard()
        self.fsm.process(guard, "critical", now=100)
        state = self.fsm.process(guard, "skipped", now=130)
        self.assertEqual(state["status"], STATE_PENDING)
        self.assertEqual(state["failure_count"], 1)

    def test_recovery_success_confirms_after_threshold(self):
        guard = _guard(success=2)
        self.fsm.process(guard, "critical", now=100)
        self.fsm.process(guard, "critical", now=130)
        state = self.fsm.start_recovery(guard["id"], now=140)
        self.assertEqual(state["status"], STATE_RECOVERING)

        state = self.fsm.process(guard, "ok", now=160)
        self.assertEqual(state["status"], STATE_RECOVERING)
        state = self.fsm.process(guard, "ok", now=190)
        self.assertEqual(state["status"], STATE_RECOVERING)

        state = self.fsm.finish_recovery(guard, True, now=200)
        self.assertEqual(state["status"], STATE_OK)
        self.assertEqual(state["recovery_rounds"], 0)

    def test_recovery_failure_gives_up_and_rearms(self):
        guard = _guard(gave_up=2)
        for index in range(2):
            self.fsm.process(guard, "critical", now=100 + index * 30)
            self.fsm.start_recovery(guard["id"], now=200 + index * 100)
            state = self.fsm.finish_recovery(guard, False, now=300 + index * 100)

        self.assertEqual(state["status"], STATE_GAVE_UP)
        self.assertGreater(state["rearm_at"], 0)

        state = self.fsm.process(guard, "critical", now=state["rearm_at"] - 1)
        self.assertEqual(state["status"], STATE_GAVE_UP)

        state = self.fsm.process(guard, "critical", now=state["rearm_at"] + 1)
        self.assertEqual(state["status"], STATE_PENDING)

    def test_failed_round_before_give_up_allows_retry(self):
        guard = _guard(gave_up=3)
        self.fsm.process(guard, "critical", now=100)
        self.fsm.process(guard, "critical", now=130)
        self.fsm.start_recovery(guard["id"], now=140)
        state = self.fsm.finish_recovery(guard, False, now=200)
        self.assertEqual(state["status"], STATE_FAILED)
        self.assertTrue(self.fsm.should_recover(guard["id"], now=201))

    def test_recovery_id_is_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "guards.json")
            fsm = GuardStateMachine(state_path=path)
            guard = _guard()
            fsm.process(guard, "critical", now=100)
            fsm.process(guard, "critical", now=130)
            fsm.start_recovery(guard["id"], now=140, recovery_id="r-1")
            self.assertEqual(fsm.snapshot(guard["id"])["last_recovery_id"], "r-1")

            restored = GuardStateMachine(state_path=path)
            self.assertEqual(restored.snapshot(guard["id"])["last_recovery_id"], "r-1")

    def test_state_is_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "guards.json")
            fsm = GuardStateMachine(state_path=path)
            guard = _guard()
            fsm.process(guard, "critical", now=100)
            fsm.process(guard, "critical", now=130)

            restored = GuardStateMachine(state_path=path)
            state = restored.snapshot(guard["id"])
            self.assertEqual(state["status"], STATE_FAILED)
            self.assertEqual(state["failure_count"], 2)
            self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
