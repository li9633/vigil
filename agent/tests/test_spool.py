"""spool 容量、seq、补传与持久化测试。"""

import json
import os
import tempfile
import unittest

from vigil_agent.spool import EventSpool


class SpoolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_assigns_seq_and_event_id(self):
        spool = EventSpool(self.dir)
        first = spool.append({"type": "module_status", "domain": "net.dns", "payload": {}})
        second = spool.append({"type": "action_result", "domain": "net.pppoe", "payload": {}})
        self.assertEqual(first["seq"], 1)
        self.assertEqual(second["seq"], 2)
        self.assertTrue(first["event_id"])
        self.assertEqual(spool.stats()["pending"], 2)
        self.assertEqual([item["seq"] for item in spool.pending(10)], [1, 2])

    def test_persists_across_reopen(self):
        spool = EventSpool(self.dir)
        spool.append({"type": "a", "domain": "x", "payload": {"n": 1}})
        spool.append({"type": "b", "domain": "x", "payload": {"n": 2}})
        del spool

        reopened = EventSpool(self.dir)
        pending = reopened.pending(10)
        self.assertEqual([item["seq"] for item in pending], [1, 2])
        self.assertEqual(pending[1]["payload"]["n"], 2)
        # seq 继续单调递增
        self.assertEqual(reopened.next_seq(), 3)

    def test_entry_limit_drops_oldest(self):
        spool = EventSpool(self.dir, max_entries=3)
        for index in range(6):
            spool.append({"type": "t", "domain": "x", "payload": {"i": index}})
        stats = spool.stats()
        self.assertEqual(stats["pending"], 3)
        self.assertEqual(stats["dropped"], 3)
        self.assertEqual([item["seq"] for item in spool.pending(10)], [4, 5, 6])
        self.assertEqual(spool.pending(10)[0]["payload"]["i"], 3)

    def test_byte_limit_drops_oldest(self):
        spool = EventSpool(self.dir, max_entries=100, max_bytes=300)
        payload = "x" * 100
        for _ in range(3):
            spool.append({"type": "t", "domain": "x", "payload": {"blob": payload}})
        stats = spool.stats()
        self.assertEqual(stats["pending"], 1)
        self.assertEqual(stats["dropped"], 2)
        self.assertLessEqual(stats["bytes"], 300)

    def test_ack_removes_entries(self):
        spool = EventSpool(self.dir)
        for index in range(3):
            spool.append({"type": "t", "domain": "x", "payload": {"i": index}})
        removed = spool.ack([1, 3])
        self.assertEqual(removed, 2)
        self.assertEqual([item["seq"] for item in spool.pending(10)], [2])
        # ack 不存在的序号不报错
        self.assertEqual(spool.ack([99]), 0)

    def test_read_latest_and_order(self):
        spool = EventSpool(self.dir)
        for index in range(5):
            spool.append({"type": "t", "domain": "x", "payload": {"i": index}})
        self.assertEqual([item["seq"] for item in spool.read(2)], [4, 5])
        self.assertEqual([item["seq"] for item in spool.read(2, newest_first=True)], [5, 4])

    def test_repairs_truncated_tail(self):
        path = os.path.join(self.dir, "events.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"seq": 1, "type": "ok", "domain": "x", "payload": {}}) + "\n")
            handle.write('{"seq": 2, "type": "broken"')
        spool = EventSpool(self.dir)
        self.assertEqual([item["seq"] for item in spool.pending(10)], [1])
        # 修复后文件应为合法 jsonl
        with open(path, "r", encoding="utf-8") as handle:
            lines = [line for line in handle.read().splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        json.loads(lines[0])

    def test_dropped_counter_persists(self):
        spool = EventSpool(self.dir, max_entries=1)
        spool.append({"type": "a", "domain": "x", "payload": {}})
        spool.append({"type": "b", "domain": "x", "payload": {}})
        self.assertEqual(spool.stats()["dropped"], 1)
        reopened = EventSpool(self.dir, max_entries=1)
        self.assertEqual(reopened.stats()["dropped"], 1)


if __name__ == "__main__":
    unittest.main()
