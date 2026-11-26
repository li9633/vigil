"""数据盘水位监控：超过阈值告警，回落后发送恢复通知。

告警状态存于 `Setting(key='disk_guard')`，worker 重启后仍能保持冷却窗口，
避免反复发送同一条磁盘告警。
"""

from __future__ import annotations

import json
import posixpath
import shutil
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db import utcnow
from app.logstore import log_event
from app.models import Setting
from app.services.notification_service import send_system_alert

SETTING_KEY = "disk_guard"
CLEAR_MARGIN_PERCENT = 5


def disk_path(database_url: str) -> str:
    """SQLite 时监控数据库所在分区，其他数据库退回根分区。"""
    for prefix in ("sqlite+pysqlite:///", "sqlite:///"):
        if database_url.startswith(prefix):
            raw = database_url[len(prefix) :]
            return posixpath.dirname(raw) or "."
    return "/"


def disk_usage(path: str) -> tuple[int, int, float]:
    usage = shutil.disk_usage(path)
    percent = (usage.used * 100.0 / usage.total) if usage.total else 0.0
    return usage.total, usage.used, percent


def _load_state(db: Session) -> dict[str, Any]:
    row = db.get(Setting, SETTING_KEY)
    if row is None:
        return {}
    try:
        data = json.loads(row.value or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(db: Session, state: dict[str, Any]) -> None:
    row = db.get(Setting, SETTING_KEY)
    if row is None:
        row = Setting(key=SETTING_KEY)
        db.add(row)
    row.value = json.dumps(state, ensure_ascii=False)
    row.updated_at = utcnow()
    db.flush()


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _fmt_gb(value: int) -> str:
    return "%.1f GB" % (value / 1024 / 1024 / 1024)


def check_disk(
    db: Session,
    now: datetime,
    path: str,
    threshold_percent: int,
    cooldown_seconds: int,
    clear_margin: int = CLEAR_MARGIN_PERCENT,
) -> str | None:
    """检查一次水位，返回 alert / recover / None。"""
    total, used, percent = disk_usage(path)
    state = _load_state(db)
    active = bool(state.get("active"))
    last_alert_at = _parse_time(state.get("last_alert_at"))
    over = percent >= threshold_percent

    if over:
        due = (
            last_alert_at is None
            or (now - last_alert_at).total_seconds() >= cooldown_seconds
        )
        if active and not due:
            return None
        body = "\n".join(
            [
                f"使用率：{percent:.1f}%（阈值 {threshold_percent}%）",
                f"已用：{_fmt_gb(used)} / {_fmt_gb(total)}",
                f"路径：{path}",
                f"时间：{now.isoformat()}",
            ]
        )
        send_system_alert(db, now, "[Vigil] 数据盘空间不足", body)
        _save_state(
            db,
            {
                "active": True,
                "last_alert_at": now.isoformat(),
                "percent": round(percent, 1),
            },
        )
        log_event(
            "warn",
            "disk.watermark",
            "数据盘水位超限",
            path=path,
            percent=round(percent, 1),
            threshold=threshold_percent,
        )
        return "alert"

    if active and percent <= threshold_percent - clear_margin:
        body = "\n".join(
            [
                f"使用率：{percent:.1f}%（已回落到安全区间）",
                f"已用：{_fmt_gb(used)} / {_fmt_gb(total)}",
                f"路径：{path}",
                f"时间：{now.isoformat()}",
            ]
        )
        send_system_alert(db, now, "[Vigil] 数据盘空间已恢复", body, level="ok")
        _save_state(
            db,
            {
                "active": False,
                "last_alert_at": state.get("last_alert_at"),
                "percent": round(percent, 1),
            },
        )
        log_event(
            "info",
            "disk.watermark",
            "数据盘水位恢复",
            path=path,
            percent=round(percent, 1),
            threshold=threshold_percent,
        )
        return "recover"

    return None
