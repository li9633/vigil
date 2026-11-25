"""间隔调度器测试。"""

import unittest

from vigil_agent.scheduler import IntervalScheduler


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.scheduler = IntervalScheduler(clock=lambda: self.now[0])

    def test_due_only_after_interval(self):
        self.scheduler.add("detect", 30)
        self.assertEqual(self.scheduler.due(), ["detect"])
        self.assertEqual(self.scheduler.due(), [])

        self.now[0] += 29.9
        self.assertEqual(self.scheduler.due(), [])
        self.now[0] += 0.2
        self.assertEqual(self.scheduler.due(), ["detect"])

    def test_initial_delay(self):
        self.scheduler.add("heartbeat", 30, initial_delay=10)
        self.assertEqual(self.scheduler.due(), [])
        self.now[0] += 10
        self.assertEqual(self.scheduler.due(), ["heartbeat"])

    def test_multiple_tasks(self):
        self.scheduler.add("fast", 5)
        self.scheduler.add("slow", 60)
        self.now[0] += 1
        self.scheduler.due()
        self.now[0] += 5
        self.assertEqual(sorted(self.scheduler.due()), ["fast"])
        self.now[0] += 60
        self.assertEqual(sorted(self.scheduler.due()), ["fast", "slow"])

    def test_next_delay(self):
        self.scheduler.add("a", 10, initial_delay=10)
        self.now[0] += 4
        self.assertAlmostEqual(self.scheduler.next_delay(), 6.0)

    def test_rejects_bad_interval(self):
        with self.assertRaises(ValueError):
            self.scheduler.add("bad", 0)


if __name__ == "__main__":
    unittest.main()
