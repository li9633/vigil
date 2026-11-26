"""服务端通用工具测试。"""

from __future__ import annotations

from datetime import UTC, datetime

from app.utils.env import env_bool, env_int, env_list, env_str
from app.utils.time import format_datetime, from_iso, iso_utc, to_iso, utcnow


def test_iso_utc() -> None:
    value = datetime(2026, 11, 26, 12, 0, 0)
    assert iso_utc(value) == "2026-11-26T12:00:00Z"
    assert iso_utc(None) is None


def test_to_iso_accepts_datetime_and_epoch() -> None:
    aware = datetime(2026, 11, 26, 12, 0, 0, tzinfo=UTC)
    assert to_iso(aware) == "2026-11-26T12:00:00Z"
    assert to_iso(aware.timestamp()) == "2026-11-26T12:00:00Z"


def test_from_iso() -> None:
    assert from_iso("2026-11-26T12:00:00Z") == datetime(2026, 11, 26, 12, 0, 0)
    assert from_iso("2026-11-26T20:00:00+08:00") == datetime(2026, 11, 26, 12, 0, 0)
    assert from_iso("bad") is None


def test_format_datetime() -> None:
    assert format_datetime(datetime(2026, 11, 26, 12, 0, 0)) == "2026-11-26 12:00:00"
    assert format_datetime("2026-11-26T12:00:00Z") == "2026-11-26 12:00:00"
    assert format_datetime(None) == "—"


def test_utcnow_is_naive() -> None:
    assert utcnow().tzinfo is None


def test_env_helpers(monkeypatch) -> None:
    monkeypatch.setenv("VIGIL_TEST_STR", " value ")
    monkeypatch.setenv("VIGIL_TEST_INT", "42")
    monkeypatch.setenv("VIGIL_TEST_BOOL", "yes")
    monkeypatch.setenv("VIGIL_TEST_LIST", "a, b ,,c")
    assert env_str("VIGIL_TEST_STR", "x") == "value"
    assert env_int("VIGIL_TEST_INT", 1) == 42
    assert env_bool("VIGIL_TEST_BOOL", False) is True
    assert env_list("VIGIL_TEST_LIST", []) == ["a", "b", "c"]


def test_env_helpers_fallback(monkeypatch) -> None:
    monkeypatch.delenv("VIGIL_TEST_MISSING", raising=False)
    assert env_str("VIGIL_TEST_MISSING", "x") == "x"
    assert env_int("VIGIL_TEST_MISSING", 7) == 7
    assert env_bool("VIGIL_TEST_MISSING", True) is True
    assert env_list("VIGIL_TEST_MISSING", ["x"]) == ["x"]
