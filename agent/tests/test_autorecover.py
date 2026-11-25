"""自动恢复决策的单元测试。"""
import time
import unittest

from vigil_agent.detect.base import STATUS_CRITICAL
from vigil_agent.main import AgentApp


class FakeAgent(AgentApp):
    def __init__(self, config):
        self.config = config
        self._action_thread = None
        self._failure_counts = {}
        self.calls = []

    def _run_action(self, layer, name, target):
        self.calls.append((layer, name, target))

    def _layer_ok(self, layer):
        return False


class AutoRecoverPlanTest(unittest.TestCase):
    def test_carrier_critical_triggers_ifup(self):
        agent = FakeAgent({"action": {"enabled": True, "failure_threshold": 3}})
        agent._failure_counts = {"net.carrier": 3}
        agent._maybe_autorecover(
            {
                "net.carrier": {"status": STATUS_CRITICAL},
                "net.pppoe": {"status": "skipped"},
            }
        )
        if agent._action_thread:
            agent._action_thread.join(timeout=1)
        time.sleep(0.01)
        self.assertEqual(agent.calls, [("net.carrier", "ifup_wan", None)])

    def test_pppoe_critical_still_triggers_ifup(self):
        agent = FakeAgent({"action": {"enabled": True, "failure_threshold": 3}})
        agent._failure_counts = {"net.pppoe": 3}
        agent._maybe_autorecover(
            {
                "net.carrier": {"status": "ok"},
                "net.pppoe": {"status": STATUS_CRITICAL},
            }
        )
        if agent._action_thread:
            agent._action_thread.join(timeout=1)
        time.sleep(0.01)
        self.assertEqual(agent.calls, [("net.pppoe", "ifup_wan", None)])

    def test_below_threshold_does_nothing(self):
        agent = FakeAgent({"action": {"enabled": True, "failure_threshold": 3}})
        agent._failure_counts = {"net.carrier": 2}
        agent._maybe_autorecover({"net.carrier": {"status": STATUS_CRITICAL}})
        self.assertEqual(agent.calls, [])

    def test_disabled_does_nothing(self):
        agent = FakeAgent({"action": {"enabled": False}})
        agent._failure_counts = {"net.carrier": 5}
        agent._maybe_autorecover({"net.carrier": {"status": STATUS_CRITICAL}})
        self.assertEqual(agent.calls, [])


if __name__ == "__main__":
    unittest.main()
