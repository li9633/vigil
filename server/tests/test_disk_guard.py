"""磁盘水位告警测试：阈值、冷却、回落恢复与系统通知落库。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app import diskguard
from app.adapters.notifiers.base import NotificationMessage, Notifier, SendResult
from app.db import session_scope, utcnow
from app.models import Notification

CONFIG = {
    "feishu": {"enabled": True, "webhook": "https://open.feishu.cn/hook/abcd"},
    "wecom": {"enabled": False, "webhook": ""},
    "email": {"enabled": False},
    "rate_limit_per_hour": 10,
}


class RecordingNotifier(Notifier):
    channel = "feishu"

    def __init__(self) -> None:
        self.messages: list[NotificationMessage] = []

    def send(self, message: NotificationMessage) -> SendResult:
        self.messages.append(message)
        return SendResult(True)


def test_disk_path_from_url() -> None:
    assert diskguard.disk_path("sqlite:////var/lib/vigil/vigil.db") == "/var/lib/vigil"
    assert diskguard.disk_path("sqlite+pysqlite:///./vigil.db") == "."
    assert diskguard.disk_path("sqlite:///vigil.db") == "."
    assert diskguard.disk_path("postgresql://db/vigil") == "/"


def test_disk_alert_cooldown_and_recovery(
    db_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    percent = {"value": 90.0}
    total = 100 * 1024**3
    calls: list[dict[str, Any]] = []

    def fake_usage(path: str) -> tuple[int, int, float]:
        used = int(percent["value"] * total / 100)
        return total, used, percent["value"]

    def fake_alert(db: Any, now: Any, title: str, body: str, **kwargs: Any) -> list[Any]:
        calls.append({"title": title, "body": body, **kwargs})
        return []

    monkeypatch.setattr(diskguard, "disk_usage", fake_usage)

    monkeypatch.setattr(diskguard, "send_system_alert", fake_alert)
    now = utcnow()

    with session_scope() as db:
        assert diskguard.check_disk(db, now, "/data", 85, 3600) == "alert"
        assert calls[0]["title"] == "[Vigil] 数据盘空间不足"

        # 冷却窗口内不重复告警
        assert diskguard.check_disk(db, now + timedelta(minutes=10), "/data", 85, 3600) is None
        assert len(calls) == 1

        # 冷却结束后再次提醒
        assert diskguard.check_disk(db, now + timedelta(hours=2), "/data", 85, 3600) == "alert"
        assert len(calls) == 2

        # 仍高于恢复阈值（85-5=80）时不发恢复
        percent["value"] = 82.0
        assert diskguard.check_disk(db, now + timedelta(hours=3), "/data", 85, 3600) is None
        assert len(calls) == 2

        # 回落后发送一次恢复
        percent["value"] = 75.0
        assert diskguard.check_disk(db, now + timedelta(hours=4), "/data", 85, 3600) == "recover"
        assert calls[-1]["title"] == "[Vigil] 数据盘空间已恢复"
        assert calls[-1]["level"] == "ok"

        # 已恢复状态下不再重复
        assert diskguard.check_disk(db, now + timedelta(hours=5), "/data", 85, 3600) is None
        assert len(calls) == 3


def test_system_alert_records_notification(
    db_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = RecordingNotifier()
    monkeypatch.setattr(
        "app.services.notification_service.build_notifiers", lambda config: [recorder]
    )
    now = utcnow()

    with session_scope() as db:
        records = diskguard.send_system_alert(
            db, now, "[Vigil] 数据盘空间不足", "使用率：91%"
        )
        assert [record.status for record in records] == ["sent"]

    assert recorder.messages[0].title == "[Vigil] 数据盘空间不足"
    with session_scope() as db:
        row = db.scalars(select(Notification)).one()
        assert row.kind == "system"
        assert row.client_id == "system"
        assert row.incident_id is None
