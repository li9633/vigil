"""通用时间工具：服务端只使用绝对时间，不生成相对时间。"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """当前 UTC 时间（naive），与 SQLite 存储格式保持一致。"""
    return datetime.now(UTC).replace(tzinfo=None)


def iso_utc(value: datetime | None) -> str | None:
    """naive UTC 时间序列化为带 Z 后缀的 ISO 字符串。"""
    return None if value is None else value.isoformat() + "Z"


def to_iso(value: datetime | str | int | float | None) -> str | None:
    """把 datetime / ISO 字符串 / epoch 统一序列化为 UTC ISO 字符串。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)
        return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if abs(seconds) >= 1e12:
            seconds /= 1000.0
        return datetime.fromtimestamp(seconds, UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, str):
        parsed = from_iso(value)
        return None if parsed is None else to_iso(parsed)
    return None


def from_iso(text: str | None) -> datetime | None:
    """解析 ISO 字符串；无时区按 UTC 处理，返回 naive UTC。"""
    if not text:
        return None
    value = text.strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(UTC).replace(tzinfo=None)


def format_datetime(
    value: datetime | str | int | float | None,
    pattern: str = "%Y-%m-%d %H:%M:%S",
    fallback: str = "—",
) -> str:
    if value is None:
        return fallback
    moment: datetime | None
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)
    else:
        moment = from_iso(value) if isinstance(value, str) else None
        if moment is None and isinstance(value, (int, float)) and not isinstance(value, bool):
            seconds = float(value)
            if abs(seconds) >= 1e12:
                seconds /= 1000.0
            moment = datetime.fromtimestamp(seconds, UTC).replace(tzinfo=None)
        if moment is None:
            return fallback
        moment = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime(pattern)
