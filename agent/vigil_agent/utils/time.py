"""通用时间工具。

约定：
- 持久化数据（日志、事件、状态）只能使用绝对时间；相对时间仅供展示。
- 相对时间默认最多回溯/前瞻 3 年，超限回退为完整日期时间。
- 月份按 30 天、年份按 365 天折算，保证跨端一致。
"""

import time
from datetime import datetime, timezone

DEFAULT_MAX_AGE_YEARS = 3
SECOND = 1
MINUTE = 60
HOUR = 3600
DAY = 86400
MONTH = 30 * DAY
YEAR = 365 * DAY


def to_datetime(value, tz=timezone.utc):
    """把 epoch 秒/毫秒、ISO 字符串或 datetime 统一转为带时区的时间。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=tz)
        return value.astimezone(tz)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if abs(seconds) >= 1e12:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=tz)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return to_datetime(int(text), tz)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        return parsed.astimezone(tz)
    return None


def now(tz=timezone.utc):
    return datetime.now(tz)


def today(tz=None):
    """当前日期；tz 为 None 时使用系统本地时区。"""
    return datetime.now(tz).date()


def current_time(tz=None):
    """当前时刻 HH:MM:SS；tz 为 None 时使用系统本地时区。"""
    return datetime.now(tz).strftime("%H:%M:%S")


def now_iso():
    return to_iso(datetime.now(timezone.utc))


def to_iso(value):
    """转成 UTC ISO8601（秒精度，带 Z）；无法解析返回 None。"""
    moment = to_datetime(value)
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_datetime(text, formats=None, tz=timezone.utc):
    """字符串转时间；formats 为空时按 ISO 与常见格式尝试。"""
    if not isinstance(text, str):
        return to_datetime(text, tz=tz)
    value = text.strip()
    if not value:
        return None
    for fmt in formats or ():
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        return parsed.astimezone(tz)
    return to_datetime(value, tz=tz)


def format_datetime(value, pattern="%Y-%m-%d %H:%M:%S", tz=timezone.utc, fallback="—"):
    moment = to_datetime(value, tz=tz)
    if moment is None:
        return fallback
    return moment.strftime(pattern)


def _max_age_seconds(max_age_years):
    return max(0.0, float(max_age_years)) * YEAR


def _past_text(seconds):
    if seconds < 10:
        return "刚刚"
    if seconds < MINUTE:
        return "%d 秒前" % int(seconds)
    if seconds < HOUR:
        return "%d 分钟前" % int(seconds / MINUTE)
    if seconds < DAY:
        return "%d 小时前" % int(seconds / HOUR)
    if seconds < MONTH:
        return "%d 天前" % int(seconds / DAY)
    if seconds < YEAR:
        return "%d 个月前" % int(seconds / MONTH)
    return "%d 年前" % int(seconds / YEAR)


def _future_text(seconds):
    if seconds < 10:
        return "即将"
    if seconds < MINUTE:
        return "%d 秒后" % int(seconds)
    if seconds < HOUR:
        return "%d 分钟后" % int(seconds / MINUTE)
    if seconds < DAY:
        return "%d 小时后" % int(seconds / HOUR)
    if seconds < MONTH:
        return "%d 天后" % int(seconds / DAY)
    if seconds < YEAR:
        return "%d 个月后" % int(seconds / MONTH)
    return "%d 年后" % int(seconds / YEAR)


def format_relative(value, now_value=None, max_age_years=DEFAULT_MAX_AGE_YEARS, fallback="—"):
    """相对过去时间；未来时间自动交给 format_relative_future。"""
    moment = to_datetime(value)
    if moment is None:
        return fallback
    current = to_datetime(now_value) or datetime.now(timezone.utc)
    diff = (current - moment).total_seconds()
    if diff < 0:
        return format_relative_future(value, now_value, max_age_years, fallback)
    if max_age_years <= 0 or diff > _max_age_seconds(max_age_years):
        return format_datetime(moment, fallback=fallback)
    return _past_text(diff)


def format_relative_future(
    value, now_value=None, max_age_years=DEFAULT_MAX_AGE_YEARS, fallback="—"
):
    """相对未来时间；过去时间自动交给 format_relative。"""
    moment = to_datetime(value)
    if moment is None:
        return fallback
    current = to_datetime(now_value) or datetime.now(timezone.utc)
    diff = (moment - current).total_seconds()
    if diff < 0:
        return format_relative(value, now_value, max_age_years, fallback)
    if max_age_years <= 0 or diff > _max_age_seconds(max_age_years):
        return format_datetime(moment, fallback=fallback)
    return _future_text(diff)


def format_duration(seconds, fallback="—"):
    if seconds is None or isinstance(seconds, bool):
        return fallback
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return fallback
    if total < 0:
        return fallback
    if total < MINUTE:
        return "%d 秒" % total
    minutes = total // MINUTE
    if minutes < 60:
        return "%d 分 %d 秒" % (minutes, total % 60)
    hours = minutes // 60
    if hours < 24:
        return "%d 小时 %d 分" % (hours, minutes % 60)
    days = hours // 24
    return "%d 天 %d 小时" % (days, hours % 24)


def monotonic_ms():
    """单调时钟毫秒，用于时延测量。"""
    return int(time.monotonic() * 1000)
