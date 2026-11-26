"""飞书群机器人 Webhook 通知。"""

from __future__ import annotations

import httpx

from app.domain.notifications.ports import NotificationMessage, Notifier, SendResult

TIMEOUT_SECONDS = 10.0


def _safe_error(exc: Exception) -> str:
    """错误信息不携带 webhook URL，避免泄露机器人地址。"""
    return f"{type(exc).__name__}: 请求失败"


class FeishuNotifier(Notifier):
    channel = "feishu"

    def __init__(self, webhook: str, timeout: float = TIMEOUT_SECONDS) -> None:
        self.webhook = webhook
        self.timeout = timeout

    def send(self, message: NotificationMessage) -> SendResult:
        payload = {"msg_type": "text", "content": {"text": message.text}}
        try:
            response = httpx.post(self.webhook, json=payload, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001 - 单渠道异常需转为失败结果
            return SendResult(False, _safe_error(exc))

        if response.status_code != 200:
            return SendResult(False, f"HTTP {response.status_code}")
        try:
            data = response.json()
        except Exception:  # noqa: BLE001
            return SendResult(False, "响应不是合法 JSON")
        if not isinstance(data, dict):
            return SendResult(False, "响应格式异常")

        if data.get("code") == 0 or data.get("StatusCode") == 0:
            return SendResult(True)

        code = data.get("code", data.get("StatusCode"))
        detail = str(data.get("msg") or data.get("StatusMessage") or "")[:200]
        return SendResult(False, f"code={code} {detail}".strip())
