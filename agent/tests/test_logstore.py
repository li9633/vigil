"""日志核心：格式化、脱敏、轮转压缩、查询与导出测试。"""

import datetime
import gzip
import json
import logging
import os
import tempfile
import unittest

from vigil_agent.logstore import (
    CompressingRotatingFileHandler,
    JsonlFormatter,
    MemoryLogHandler,
    export_log_events,
    filter_events,
    read_log_events,
    record_to_event,
    reset_log_context,
    set_log_context,
)


class RecordingState:
    def __init__(self):
        self.logs = []

    def add_log(self, item):
        self.logs.append(item)


class FormatTest(unittest.TestCase):
    def tearDown(self):
        reset_log_context()

    def test_envelope_and_context(self):
        set_log_context("cl_test", "boot-1")
        record = logging.LogRecord("vigil.agent", logging.INFO, __file__, 1, "hello %s", ("x",), None)
        record.vigil = {
            "event": "guard.state",
            "guard": {"id": "g1", "name": "网络"},
            "status": "ok",
        }
        event = record_to_event(record)
        self.assertEqual(event["v"], 1)
        self.assertEqual(event["origin"], "agent")
        self.assertEqual(event["event"], "guard.state")
        self.assertEqual(event["msg"], "hello x")
        self.assertEqual(event["guard"]["id"], "g1")
        self.assertEqual(event["client_id"], "cl_test")
        self.assertEqual(event["boot_id"], "boot-1")
        self.assertGreaterEqual(event["uptime_ms"], 0)
        self.assertRegex(event["ts"], r"^\d{4}-\d{2}-\d{2}T.*\.\d{3}[+-]\d{2}:\d{2}$")
        again = record_to_event(record)
        self.assertEqual(again["seq"], event["seq"])

    def test_redaction_and_truncation(self):
        record = logging.LogRecord(
            "vigil.agent", logging.WARNING, __file__, 1, "auth Bearer abc123", (), None
        )
        record.vigil = {
            "event": "probe.error",
            "secret": "top-secret",
            "webhook": "https://example.com/hook?token=1",
            "evidence": {"text": "x" * 2000},
            "note": "token=abcdef&x=1",
        }
        event = record_to_event(record)
        self.assertEqual(event["secret"], "***")
        self.assertEqual(event["webhook"], "***")
        self.assertNotIn("abc123", event["msg"])
        self.assertNotIn("abcdef", event["note"])
        self.assertTrue(event["evidence"]["_truncated"])
        self.assertLessEqual(len(event["evidence"]["text"]), 1024)

    def test_memory_handler_stores_dict(self):
        state = RecordingState()
        handler = MemoryLogHandler(state)
        record = logging.LogRecord("vigil.agent", logging.INFO, __file__, 1, "hi", (), None)
        record.vigil = {"event": "agent.start"}
        handler.emit(record)
        self.assertEqual(state.logs[0]["event"], "agent.start")
        self.assertIsInstance(state.logs[0]["ts"], str)


class FilterTest(unittest.TestCase):
    def setUp(self):
        self.items = [
            {
                "ts": "2026-01-01T00:00:00.000+00:00",
                "level": "info",
                "event": "guard.state",
                "guard": {"id": "a"},
            },
            {
                "ts": "2026-01-01T00:00:01.000+00:00",
                "level": "warn",
                "event": "guard.recover.start",
                "guard": {"id": "b"},
            },
            {
                "ts": "2026-01-01T00:00:02.000+00:00",
                "level": "error",
                "event": "action.result",
                "guard": {"id": "a"},
            },
        ]

    def test_level_event_guard(self):
        self.assertEqual(len(filter_events(self.items, level="warn")), 2)
        self.assertEqual(len(filter_events(self.items, event="guard.")), 2)
        self.assertEqual(len(filter_events(self.items, guard_id="a")), 2)
        self.assertEqual(len(filter_events(self.items, guard_id="a", level="error")), 1)

    def test_since_and_limit(self):
        since = datetime.datetime.fromisoformat(
            "2026-01-01T00:00:01.500+00:00"
        ).timestamp()
        self.assertEqual([item["level"] for item in filter_events(self.items, since=since)], ["error"])
        self.assertEqual(len(filter_events(self.items, limit=1)), 1)


class RotationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "vigil.log")
        self.logger = logging.getLogger("test.logstore.rotation")
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False

    def tearDown(self):
        for handler in list(self.logger.handlers):
            handler.close()
            self.logger.removeHandler(handler)
        self.tmp.cleanup()

    def _handler(self, **kwargs):
        handler = CompressingRotatingFileHandler(self.path, **kwargs)
        handler.setFormatter(JsonlFormatter())
        self.logger.addHandler(handler)
        return handler

    def _write(self, count):
        for index in range(count):
            self.logger.info("line %s", index, extra={"vigil": {"event": "test.line", "index": index}})

    def test_rotation_compresses_and_keeps_recent(self):
        handler = self._handler(max_bytes=400, backup_count=2, compress=True)
        self._write(40)
        handler.close()
        self.assertTrue(os.path.exists(self.path + ".1.gz"))
        self.assertTrue(os.path.exists(self.path + ".2.gz"))
        self.assertFalse(os.path.exists(self.path + ".3.gz"))
        if os.name != "nt":
            self.assertEqual(os.stat(self.path + ".1.gz").st_mode & 0o777, 0o600)

        events = read_log_events(self.path, limit=200, backup_count=2)
        self.assertTrue(events)
        self.assertEqual(events[-1]["event"], "test.line")
        self.assertEqual(events[-1]["index"], 39)

        with gzip.open(self.path + ".1.gz", "rt", encoding="utf-8") as handle:
            first = json.loads(handle.readline())
        self.assertEqual(first["event"], "test.line")

    def test_rotation_without_compression_keeps_plain(self):
        handler = self._handler(max_bytes=300, backup_count=2, compress=False)
        self._write(20)
        handler.close()
        self.assertTrue(os.path.exists(self.path + ".1"))
        self.assertFalse(os.path.exists(self.path + ".1.gz"))
        self.assertTrue(read_log_events(self.path, limit=100, backup_count=2))

    def test_export_reads_archives_in_order(self):
        handler = self._handler(max_bytes=400, backup_count=2, compress=True)
        self._write(40)
        handler.close()
        data = export_log_events(self.path, backup_count=2, max_lines=500)
        lines = [json.loads(line) for line in data.decode("utf-8").splitlines() if line]
        self.assertTrue(lines)
        self.assertEqual(lines[-1]["index"], 39)
        indexes = [item["index"] for item in lines]
        self.assertEqual(indexes, sorted(indexes))


if __name__ == "__main__":
    unittest.main()
