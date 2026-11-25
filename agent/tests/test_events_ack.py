"""事件上报确认（ack）映射测试。"""

import tempfile
import unittest

from vigil_agent.main import AgentApp, _acked_seqs
from vigil_agent.spool import EventSpool


class AckedSeqsTest(unittest.TestCase):
    def setUp(self):
        self.batch = [
            {"event_id": "e-1", "seq": 11, "type": "module_status"},
            {"event_id": "e-2", "seq": 12, "type": "action_result"},
        ]

    def test_event_ids_map_to_seq(self):
        self.assertEqual(_acked_seqs(self.batch, ["e-2", "e-1"]), [12, 11])

    def test_integer_seqs_pass_through(self):
        self.assertEqual(_acked_seqs(self.batch, [12]), [12])

    def test_unknown_ack_is_ignored(self):
        self.assertEqual(_acked_seqs(self.batch, ["missing"]), [])

    def test_missing_ack_confirms_whole_batch(self):
        self.assertEqual(_acked_seqs(self.batch, None), [11, 12])
        self.assertEqual(_acked_seqs(self.batch, []), [11, 12])


class FlushSpoolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.spool = EventSpool(self.tmp.name)
        self.first = self.spool.append({"type": "module_status", "domain": "net.dns", "payload": {}})
        self.second = self.spool.append({"type": "action_result", "domain": "net.pppoe", "payload": {}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_server_event_id_ack_clears_spool(self):
        class FakeTransport:
            def __init__(self, event_ids):
                self.event_ids = event_ids
                self.calls = []

            def events(self, batch):
                self.calls.append(batch)
                return {
                    "ok": True,
                    "status": 200,
                    "headers": {},
                    "body": {"acked": self.event_ids},
                    "error": "",
                }

        agent = AgentApp.__new__(AgentApp)
        agent.spool = self.spool
        agent.transport = FakeTransport([self.first["event_id"], self.second["event_id"]])
        agent.flush_spool()

        self.assertEqual(len(agent.transport.calls), 1)
        self.assertEqual(self.spool.pending(10), [])


if __name__ == "__main__":
    unittest.main()
