"""通用工具单元测试。"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from vigil_agent.utils import fs, proc, validation
from vigil_agent.utils.time import (
    format_datetime,
    format_duration,
    format_relative,
    format_relative_future,
    parse_datetime,
    to_iso,
)

NOW = datetime(2026, 11, 26, 12, 0, 0, tzinfo=timezone.utc)


class TimeUtilsTest(unittest.TestCase):
    def test_iso_roundtrip(self):
        self.assertEqual(to_iso(NOW), "2026-11-26T12:00:00Z")
        parsed = parse_datetime("2026-11-26T12:00:00Z")
        self.assertEqual(to_iso(parsed), "2026-11-26T12:00:00Z")
        self.assertEqual(to_iso(NOW.timestamp()), "2026-11-26T12:00:00Z")

    def test_relative_past_boundaries(self):
        cases = [
            (timedelta(seconds=5), "刚刚"),
            (timedelta(seconds=30), "30 秒前"),
            (timedelta(minutes=5), "5 分钟前"),
            (timedelta(hours=2), "2 小时前"),
            (timedelta(days=3), "3 天前"),
            (timedelta(days=40), "1 个月前"),
            (timedelta(days=400), "1 年前"),
        ]
        for delta, expected in cases:
            with self.subTest(delta=delta):
                value = NOW - delta
                self.assertEqual(format_relative(value, NOW), expected)

    def test_relative_future_boundaries(self):
        cases = [
            (timedelta(seconds=5), "即将"),
            (timedelta(seconds=30), "30 秒后"),
            (timedelta(minutes=5), "5 分钟后"),
            (timedelta(hours=2), "2 小时后"),
            (timedelta(days=3), "3 天后"),
            (timedelta(days=40), "1 个月后"),
            (timedelta(days=400), "1 年后"),
        ]
        for delta, expected in cases:
            with self.subTest(delta=delta):
                value = NOW + delta
                self.assertEqual(format_relative_future(value, NOW), expected)

    def test_relative_falls_back_to_absolute(self):
        old = NOW - timedelta(days=4 * 365)
        self.assertEqual(format_relative(old, NOW), format_datetime(old))
        future = NOW + timedelta(days=4 * 365)
        self.assertEqual(format_relative_future(future, NOW), format_datetime(future))
        self.assertEqual(format_relative(old, NOW, max_age_years=0), format_datetime(old))

    def test_relative_handles_opposite_direction(self):
        self.assertEqual(format_relative(NOW + timedelta(minutes=5), NOW), "5 分钟后")
        self.assertEqual(format_relative_future(NOW - timedelta(minutes=5), NOW), "5 分钟前")

    def test_duration(self):
        self.assertEqual(format_duration(5), "5 秒")
        self.assertEqual(format_duration(65), "1 分 5 秒")
        self.assertEqual(format_duration(3725), "1 小时 2 分")
        self.assertEqual(format_duration(90000), "1 天 1 小时")
        self.assertEqual(format_duration(None), "—")


class ProcUtilsTest(unittest.TestCase):
    def test_success(self):
        result = proc.run_command([sys.executable, "-c", "print('ok')"], timeout=5)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout.strip(), "ok")
        self.assertFalse(result.timed_out)

    def test_timeout(self):
        result = proc.run_command(
            [sys.executable, "-c", "import time; time.sleep(2)"], timeout=0.3
        )
        self.assertTrue(result.timed_out)

    def test_output_truncated(self):
        result = proc.run_command(
            [sys.executable, "-c", "print('x' * 100)"], timeout=5, max_output=10
        )
        self.assertEqual(len(result.stdout), 10)
        self.assertTrue(result.truncated)

    def test_rejects_shell_string(self):
        with self.assertRaises(TypeError):
            proc.run_command("echo ok")


class FsUtilsTest(unittest.TestCase):
    def test_atomic_write_and_ensure_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nested", "state.json")
            fs.atomic_write(path, '{"ok": true}')
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), '{"ok": true}')
            self.assertTrue(os.path.isdir(os.path.dirname(path)))

    def test_safe_join(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.realpath(tmp)
            self.assertEqual(fs.safe_join(root, "a", "b"), os.path.join(root, "a", "b"))
            with self.assertRaises(ValueError):
                fs.safe_join(root, "..", "outside")


class ValidationUtilsTest(unittest.TestCase):
    def test_check_id(self):
        self.assertTrue(validation.is_check_id("wan-link"))
        self.assertTrue(validation.is_check_id("local.acme.upstream"))
        self.assertFalse(validation.is_check_id("Bad Id"))
        self.assertFalse(validation.is_check_id(""))

    def test_topological_sort(self):
        order = validation.topological_sort({"api": ["dns"], "dns": ["wan"], "wan": []})
        self.assertEqual(order, ["wan", "dns", "api"])

    def test_cycle_detected(self):
        with self.assertRaises(validation.ValidationError):
            validation.topological_sort({"a": ["b"], "b": ["a"]})

    def test_dependency_closure(self):
        graph = {"api": ["dns"], "dns": ["wan"], "wan": []}
        self.assertEqual(validation.dependency_closure(["api"], graph), ["api", "dns", "wan"])


if __name__ == "__main__":
    unittest.main()
