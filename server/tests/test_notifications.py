"""通知能力测试：适配器、配置掩码、测试发送、去重、重试、限流与故障隔离。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import session_scope, utcnow
from app.models import AuditLog, Client, Incident, Notification
from app.notifications import load_config, retry_failed, send_incident
from app.notifiers.base import NotificationMessage, Notifier, SendResult
from app.notifiers.email import EmailNotifier
from app.notifiers.feishu import FeishuNotifier
from app.notifiers.wecom import WeComNotifier
from app.worker import _dispatch_notifications

ENV_NAMES = (
    "VIGIL_FEISHU_WEBHOOK",
    "VIGIL_WECOM_WEBHOOK",
    "VIGIL_SMTP_HOST",
    "VIGIL_SMTP_PORT",
    "VIGIL_SMTP_USERNAME",
    "VIGIL_SMTP_PASSWORD",
    "VIGIL_SMTP_FROM",
    "VIGIL_SMTP_TO",
    "VIGIL_SMTP_SECURITY",
)

CONFIG = {
    "feishu": {"enabled": True, "webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/token"},
    "wecom": {"enabled": False, "webhook": ""},
    "email": {"enabled": False},
    "rate_limit_per_hour": 10,
}


@pytest.fixture(autouse=True)
def _clear_notify_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self._payload


class RecordingNotifier(Notifier):
    channel = "feishu"

    def __init__(self) -> None:
        self.messages: list[NotificationMessage] = []

    def send(self, message: NotificationMessage) -> SendResult:
        self.messages.append(message)
        return SendResult(True)


class FailingNotifier(Notifier):
    channel = "wecom"

    def send(self, message: NotificationMessage) -> SendResult:
        raise RuntimeError("boom")


# --------------------------------------------------------------------------
# 适配器：payload 与成功判定
# --------------------------------------------------------------------------


def test_feishu_payload_and_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> FakeResponse:
        captured.update(url=url, payload=json, timeout=timeout)
        return FakeResponse({"code": 0, "msg": "success"})

    monkeypatch.setattr("app.notifiers.feishu.httpx.post", fake_post)
    notifier = FeishuNotifier("https://open.feishu.cn/open-apis/bot/v2/hook/token")

    result = notifier.send(NotificationMessage(title="[Vigil] 离线", body="正文"))

    assert result.ok is True
    assert result.error == ""
    assert captured["payload"] == {
        "msg_type": "text",
        "content": {"text": "[Vigil] 离线\n正文"},
    }
    assert captured["timeout"] == 10


def test_feishu_legacy_status_code_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.notifiers.feishu.httpx.post",
        lambda *args, **kwargs: FakeResponse({"StatusCode": 0, "StatusMessage": "success"}),
    )
    assert FeishuNotifier("https://example.com/hook").send(
        NotificationMessage(title="t", body="b")
    ).ok is True

    monkeypatch.setattr(
        "app.notifiers.feishu.httpx.post",
        lambda *args, **kwargs: FakeResponse({"code": 19021, "msg": "sign match fail"}),
    )
    failed = FeishuNotifier("https://example.com/hook").send(
        NotificationMessage(title="t", body="b")
    )
    assert failed.ok is False
    assert "19021" in failed.error


def test_feishu_network_error_does_not_leak_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(*args: Any, **kwargs: Any) -> FakeResponse:
        raise RuntimeError("connect failed https://open.feishu.cn/secret-token")

    monkeypatch.setattr("app.notifiers.feishu.httpx.post", fake_post)
    result = FeishuNotifier("https://open.feishu.cn/secret-token").send(
        NotificationMessage(title="t", body="b")
    )
    assert result.ok is False
    assert "secret-token" not in result.error


def test_wecom_payload_and_judgement(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> FakeResponse:
        captured.update(payload=json, timeout=timeout)
        return FakeResponse({"errcode": 0, "errmsg": "ok"})

    monkeypatch.setattr("app.notifiers.wecom.httpx.post", fake_post)
    notifier = WeComNotifier("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x")

    result = notifier.send(NotificationMessage(title="[Vigil] 离线", body="正文"))

    assert result.ok is True
    assert captured["payload"]["msgtype"] == "markdown"
    assert captured["payload"]["markdown"]["content"] == "**[Vigil] 离线**\n正文"

    monkeypatch.setattr(
        "app.notifiers.wecom.httpx.post",
        lambda *args, **kwargs: FakeResponse({"errcode": 93000, "errmsg": "invalid webhook"}),
    )
    failed = notifier.send(NotificationMessage(title="t", body="b"))
    assert failed.ok is False
    assert "93000" in failed.error


# --------------------------------------------------------------------------
# 邮件
# --------------------------------------------------------------------------


def test_email_ssl_sends_utf8_message(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    class FakeSMTPSSL:
        def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
            sent.update(host=host, port=port, timeout=timeout)

        def __enter__(self) -> "FakeSMTPSSL":
            return self

        def __exit__(self, *exc: Any) -> bool:
            return False

        def login(self, username: str, password: str) -> None:
            sent["login"] = (username, password)

        def send_message(self, message: Any) -> None:
            sent["message"] = message

    monkeypatch.setattr("app.notifiers.email.smtplib.SMTP_SSL", FakeSMTPSSL)
    notifier = EmailNotifier(
        host="smtp.example.com",
        port=465,
        username="user",
        password="pass",
        sender="vigil@example.com",
        recipients=["a@example.com", "b@example.com"],
    )

    result = notifier.send(NotificationMessage(title="[Vigil] 客户端离线", body="正文内容"))

    assert result.ok is True
    assert sent["host"] == "smtp.example.com"
    assert sent["login"] == ("user", "pass")
    message = sent["message"]
    assert message["To"] == "a@example.com, b@example.com"
    assert message["Subject"] == "[Vigil] 客户端离线"
    assert "=?utf-8?" in message.as_string().lower()
    assert message.get_content().strip() == "正文内容"


def test_email_starttls_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {"starttls": 0}

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
            calls["host"] = host

        def __enter__(self) -> "FakeSMTP":
            return self

        def __exit__(self, *exc: Any) -> bool:
            return False

        def starttls(self) -> None:
            calls["starttls"] += 1

        def login(self, username: str, password: str) -> None:
            calls["login"] = (username, password)

        def send_message(self, message: Any) -> None:
            calls["sent"] = True

    monkeypatch.setattr("app.notifiers.email.smtplib.SMTP", FakeSMTP)
    notifier = EmailNotifier(
        host="smtp.example.com",
        port=587,
        sender="vigil@example.com",
        recipients=["a@example.com"],
        security="starttls",
    )
    assert notifier.send(NotificationMessage(title="t", body="b")).ok is True
    assert calls["starttls"] == 1
    assert calls["sent"] is True

    class BrokenSMTP(FakeSMTP):
        def send_message(self, message: Any) -> None:
            raise RuntimeError("smtp auth failed for pass")

    monkeypatch.setattr("app.notifiers.email.smtplib.SMTP", BrokenSMTP)
    failed = notifier.send(NotificationMessage(title="t", body="b"))
    assert failed.ok is False
    assert "pass" not in failed.error


# --------------------------------------------------------------------------
# 配置：掩码 / 保留原值 / 校验
# --------------------------------------------------------------------------


def _login(client: TestClient) -> None:
    assert client.post(
        "/api/v1/admin/login", json={"username": "admin", "password": "test-pass"}
    ).status_code == 200


def test_get_notifications_defaults_and_env_fallback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _login(client)
    data = client.get("/api/v1/admin/notifications").json()
    assert data["config"]["feishu"] == {"enabled": False, "webhook": ""}
    assert data["config"]["email"]["security"] == "ssl"
    assert data["config"]["rate_limit_per_hour"] == 10
    assert data["records"] == []

    monkeypatch.setenv("VIGIL_FEISHU_WEBHOOK", "https://open.feishu.cn/hook/abcdef")
    fallback = client.get("/api/v1/admin/notifications").json()["config"]
    assert fallback["feishu"]["enabled"] is True
    assert fallback["feishu"]["webhook"] == "****cdef"


def test_put_notifications_masks_and_keeps_secret(client: TestClient) -> None:
    _login(client)
    payload = {
        "feishu": {"enabled": True, "webhook": "https://example.com/hook/abcd1234"},
        "wecom": {"enabled": False, "webhook": ""},
        "email": {
            "enabled": True,
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "username": "user",
            "password": "super-secret",
            "from": "vigil@example.com",
            "to": "a@example.com,b@example.com",
            "security": "ssl",
        },
        "rate_limit_per_hour": 5,
    }
    response = client.put("/api/v1/admin/notifications", json=payload)
    assert response.status_code == 200, response.text
    masked = response.json()["config"]
    assert masked["feishu"]["webhook"] == "****1234"
    assert masked["email"]["password"] == "****cret"

    # 前端回传掩码值表示保持原值
    payload["feishu"]["webhook"] = "****1234"
    payload["email"]["password"] = "****cret"
    assert client.put("/api/v1/admin/notifications", json=payload).status_code == 200

    with session_scope() as db:
        stored = load_config(db)
    assert stored["feishu"]["webhook"] == "https://example.com/hook/abcd1234"
    assert stored["email"]["password"] == "super-secret"
    assert stored["rate_limit_per_hour"] == 5


def test_put_notifications_validation_errors(client: TestClient) -> None:
    _login(client)

    bad_webhook = client.put(
        "/api/v1/admin/notifications",
        json={"feishu": {"enabled": True, "webhook": "ftp://example.com/hook"}},
    )
    assert bad_webhook.status_code == 400

    missing_host = client.put(
        "/api/v1/admin/notifications",
        json={
            "email": {
                "enabled": True,
                "smtp_host": "",
                "from": "vigil@example.com",
                "to": "a@example.com",
            }
        },
    )
    assert missing_host.status_code == 400

    with session_scope() as db:
        actions = db.scalars(select(AuditLog.action)).all()
    assert "update_notifications" not in actions


def test_test_notification_endpoint(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _login(client)
    client.put(
        "/api/v1/admin/notifications",
        json={
            "feishu": {"enabled": True, "webhook": "https://example.com/hook/x"},
            "wecom": {"enabled": False, "webhook": ""},
            "email": {"enabled": False},
            "rate_limit_per_hour": 10,
        },
    )

    recorder = RecordingNotifier()
    monkeypatch.setattr("app.api_admin.build_notifiers", lambda config: [recorder])

    response = client.post("/api/v1/admin/notifications/test", json={"channel": "feishu"})
    assert response.status_code == 200, response.text
    assert response.json()["results"] == [{"channel": "feishu", "ok": True, "error": ""}]
    assert recorder.messages[0].title == "[Vigil] 通知测试"
    assert "飞书" in recorder.messages[0].body

    records = client.get("/api/v1/admin/notifications/records").json()["records"]
    assert records[0]["kind"] == "test"
    assert records[0]["status"] == "sent"

    with session_scope() as db:
        actions = db.scalars(select(AuditLog.action)).all()
    assert "test_notification" in actions


def test_test_notification_unconfigured_returns_400(client: TestClient) -> None:
    _login(client)
    response = client.post("/api/v1/admin/notifications/test", json={"channel": "feishu"})
    assert response.status_code == 400


# --------------------------------------------------------------------------
# 发送：去重 / 限流 / 故障隔离 / 重试
# --------------------------------------------------------------------------


def _seed_incident(
    client_id: str = "cl_notify",
    *,
    state: str = "DOWN",
    notified_at: Any = None,
    recovered_at: Any = None,
) -> int:
    started = utcnow() - timedelta(seconds=300)
    with session_scope() as db:
        db.add(
            Client(
                client_id=client_id,
                name="路由器 A",
                secret_sha256="0" * 64,
                last_seen=started,
                last_beacon=started,
                wan_ip="203.0.113.9",
            )
        )
        incident = Incident(
            client_id=client_id,
            category="agent_down",
            state=state,
            started_at=started,
            detected_at=started + timedelta(seconds=150),
            recovered_at=recovered_at,
            notified_at=notified_at,
            summary="agent_down: 心跳缺失 300s",
            evidence_json="{}",
        )
        db.add(incident)
        db.flush()
        return incident.id


def test_down_notification_only_once(
    db_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident_id = _seed_incident()
    recorder = RecordingNotifier()
    monkeypatch.setattr("app.notifications.build_notifiers", lambda config: [recorder])

    with session_scope() as db:
        incident = db.get(Incident, incident_id)
        now = utcnow()
        first = send_incident(db, incident, "down", now, CONFIG)
        assert [record.status for record in first] == ["sent"]
        assert incident.notified_at is not None

        second = send_incident(db, incident, "down", now, CONFIG)
        assert second == []

    assert len(recorder.messages) == 1
    assert "客户端离线" in recorder.messages[0].title
    assert "203.0.113.9" in recorder.messages[0].body


def test_recovery_requires_sent_down_and_sends_once(
    db_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = RecordingNotifier()
    monkeypatch.setattr("app.notifications.build_notifiers", lambda config: [recorder])
    now = utcnow()

    # 未发过 DOWN：不发恢复
    silent_id = _seed_incident(client_id="cl_silent", state="RECOVERED", notified_at=None)
    with session_scope() as db:
        assert send_incident(db, db.get(Incident, silent_id), "recovery", now, CONFIG) == []
    assert recorder.messages == []

    # 发过 DOWN：只发一次恢复
    recovered_id = _seed_incident(
        client_id="cl_recovered",
        state="RECOVERED",
        notified_at=now - timedelta(minutes=5),
        recovered_at=now,
    )
    with session_scope() as db:
        incident = db.get(Incident, recovered_id)
        first = send_incident(db, incident, "recovery", now, CONFIG)
        assert [record.status for record in first] == ["sent"]
        assert send_incident(db, incident, "recovery", now, CONFIG) == []

    assert len(recorder.messages) == 1
    assert "已恢复" in recorder.messages[0].title
    assert "持续时长" in recorder.messages[0].body


def test_rate_limit_marks_skipped(
    db_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident_id = _seed_incident()
    recorder = RecordingNotifier()
    monkeypatch.setattr("app.notifications.build_notifiers", lambda config: [recorder])
    now = utcnow()

    with session_scope() as db:
        db.add(
            Notification(
                incident_id=None,
                client_id="cl_notify",
                channel="feishu",
                target="open.feishu.cn",
                kind="down",
                status="sent",
                attempts=1,
                created_at=now - timedelta(minutes=5),
                sent_at=now - timedelta(minutes=5),
            )
        )

    config = {**CONFIG, "rate_limit_per_hour": 1}
    with session_scope() as db:
        records = send_incident(db, db.get(Incident, incident_id), "down", now, config)

    assert [record.status for record in records] == ["skipped"]
    assert records[0].last_error == "rate_limited"
    assert records[0].sent_at is None
    assert recorder.messages == []

    # 再跑一轮不应重复插入 skipped 记录
    with session_scope() as db:
        again = send_incident(db, db.get(Incident, incident_id), "down", now, config)
    assert again == []

    with session_scope() as db:
        count = db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.kind == "down", Notification.status == "skipped")
        )
    assert count == 1


def test_channel_failure_does_not_block_others(
    db_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident_id = _seed_incident()
    recorder = RecordingNotifier()
    monkeypatch.setattr(
        "app.notifications.build_notifiers",
        lambda config: [FailingNotifier(), recorder],
    )

    with session_scope() as db:
        incident = db.get(Incident, incident_id)
        records = send_incident(db, incident, "down", utcnow(), CONFIG)
        assert incident.notified_at is not None

    statuses = {record.channel: record.status for record in records}
    assert statuses == {"wecom": "failed", "feishu": "sent"}
    failed = next(record for record in records if record.channel == "wecom")
    assert "RuntimeError" in (failed.last_error or "")
    assert failed.next_attempt_at is not None


def test_retry_failed_increments_attempts(
    db_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident_id = _seed_incident()
    recorder = RecordingNotifier()
    monkeypatch.setattr("app.notifications.build_notifiers", lambda config: [recorder])
    now = utcnow()

    with session_scope() as db:
        db.add(
            Notification(
                incident_id=incident_id,
                client_id="cl_notify",
                channel="feishu",
                target="open.feishu.cn",
                kind="down",
                status="failed",
                attempts=1,
                last_error="HTTP 500",
                next_attempt_at=now - timedelta(seconds=1),
                created_at=now - timedelta(minutes=2),
            )
        )

    with session_scope() as db:
        retried = retry_failed(db, now, CONFIG)
        assert len(retried) == 1
        assert retried[0].attempts == 2
        assert retried[0].status == "sent"
        assert retried[0].next_attempt_at is None
        incident = db.get(Incident, incident_id)
        assert incident.notified_at is not None
    assert len(recorder.messages) == 1


def test_retry_stops_after_three_attempts(
    db_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident_id = _seed_incident(notified_at=utcnow())
    recorder = RecordingNotifier()
    monkeypatch.setattr("app.notifications.build_notifiers", lambda config: [recorder])
    now = utcnow()

    with session_scope() as db:
        db.add(
            Notification(
                incident_id=incident_id,
                client_id="cl_notify",
                channel="feishu",
                target="open.feishu.cn",
                kind="down",
                status="failed",
                attempts=3,
                last_error="HTTP 500",
                next_attempt_at=now - timedelta(seconds=1),
                created_at=now - timedelta(minutes=5),
            )
        )

    with session_scope() as db:
        assert retry_failed(db, now, CONFIG) == []
        record = db.scalars(select(Notification)).one()
        assert record.attempts == 3
    assert recorder.messages == []


def test_worker_dispatches_recovery_without_touched(
    db_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """恢复由心跳接口写入，不经过 scan_once；worker 仍需补发恢复通知。"""
    now = utcnow()
    incident_id = _seed_incident(
        client_id="cl_worker_recovery",
        state="RECOVERED",
        notified_at=now - timedelta(minutes=5),
        recovered_at=now - timedelta(seconds=10),
    )
    recorder = RecordingNotifier()
    monkeypatch.setattr("app.notifications.build_notifiers", lambda config: [recorder])
    monkeypatch.setattr("app.worker.load_config", lambda db: CONFIG)

    with session_scope() as db:
        _dispatch_notifications(db, [], now)
    assert len(recorder.messages) == 1
    assert "已恢复" in recorder.messages[0].title

    with session_scope() as db:
        record = db.scalars(
            select(Notification).where(
                Notification.incident_id == incident_id,
                Notification.kind == "recovery",
            )
        ).one()
        assert record.status == "sent"

    # 再跑一轮不应重复发送
    with session_scope() as db:
        _dispatch_notifications(db, [], now)
    assert len(recorder.messages) == 1
