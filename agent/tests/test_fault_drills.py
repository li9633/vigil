"""M8-7 故障演练：断网、重启、磁盘满与补传恢复。"""

import tempfile
import unittest

from vigil_agent.services import uplink
from vigil_agent.spool import EventSpool


class FakeState:
    def __init__(self):
        self.last_heartbeat = 0
        self._checks = {}

    def snapshot_checks(self):
        return dict(self._checks)

    def snapshot_guards(self):
        return {}


class FakeRegistry:
    def catalog(self):
        return []


class FakeTransport:
    def __init__(self):
        self.is_registered = True
        self.heartbeat_ok = True
        self.events_ok = True
        self.heartbeat_calls = 0
        self.event_batches = []

    def heartbeat(self, *args, **kwargs):
        self.heartbeat_calls += 1
        if self.heartbeat_ok:
            return {"ok": True, "body": {}}
        return {"ok": False, "error": "network unreachable"}

    def events(self, batch):
        self.event_batches.append([dict(item) for item in batch])
        if not self.events_ok:
            return {"ok": False, "error": "network unreachable"}
        return {"ok": True, "body": {"acked": [item["event_id"] for item in batch]}}


class FakeApp:
    def __init__(self, spool, transport):
        self.spool = spool
        self.transport = transport
        self.state = FakeState()
        self.registry = FakeRegistry()
        self.checks = []
        self.guards = []
        self.warnings = []

    def _log_throttled(self, key, level, event, message, **fields):
        self.warnings.append((level, event, fields))


class SpoolRestartTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_pending_events_survive_restart_and_seq_continues(self):
        first = EventSpool(self.dir)
        one = first.append({"type": "check_status", "payload": {"n": 1}})
        two = first.append({"type": "check_status", "payload": {"n": 2}})
        self.assertEqual([item["seq"] for item in first.pending()], [one["seq"], two["seq"]])

        second = EventSpool(self.dir)
        pending = second.pending()
        self.assertEqual([item["event_id"] for item in pending], [one["event_id"], two["event_id"]])
        third = second.append({"type": "check_status", "payload": {"n": 3}})
        self.assertGreater(third["seq"], two["seq"])

    def test_append_survives_disk_full(self):
        spool = EventSpool(self.dir)
        # 把目标路径指向目录，模拟写入失败（等价磁盘满/只读）
        spool.path = spool.spool_dir
        entry = spool.append({"type": "check_status"})
        self.assertIn("event_id", entry)
        self.assertEqual(spool.pending(), [])
        self.assertEqual(spool.stats()["dropped"], 1)

    def test_ack_survives_rewrite_failure(self):
        spool = EventSpool(self.dir)
        entry = spool.append({"type": "check_status"})
        original = spool._entries

        def fail():
            raise OSError("disk full")

        spool._rewrite = fail
        self.assertEqual(spool.ack([entry["seq"]]), 0)
        self.assertEqual([item["event_id"] for item in spool.pending()], [entry["event_id"]])
        self.assertEqual([item["event_id"] for item in spool._entries], [
            item["event_id"] for item in original
        ])


class NetworkOutageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.spool = EventSpool(self.tmp.name)
        self.transport = FakeTransport()
        self.app = FakeApp(self.spool, self.transport)

    def tearDown(self):
        self.tmp.cleanup()

    def test_outage_accumulates_then_flushes_on_recovery(self):
        for index in range(3):
            self.spool.append({"type": "check_status", "payload": {"n": index}})

        self.transport.heartbeat_ok = False
        uplink.heartbeat_once(self.app)
        self.assertEqual(self.spool.stats()["pending"], 3)
        self.assertEqual(self.transport.event_batches, [])
        self.assertTrue(self.app.warnings)

        self.transport.heartbeat_ok = True
        uplink.heartbeat_once(self.app)
        self.assertEqual(self.spool.stats()["pending"], 0)
        self.assertEqual(len(self.transport.event_batches), 1)
        self.assertEqual(len(self.transport.event_batches[0]), 3)

    def test_event_upload_failure_keeps_pending(self):
        for index in range(2):
            self.spool.append({"type": "check_status", "payload": {"n": index}})

        self.transport.events_ok = False
        uplink.heartbeat_once(self.app)
        self.assertEqual(self.spool.stats()["pending"], 2)

        self.transport.events_ok = True
        uplink.heartbeat_once(self.app)
        self.assertEqual(self.spool.stats()["pending"], 0)


if __name__ == "__main__":
    unittest.main()
