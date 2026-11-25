"""企业微信群机器人 Webhook 通知（markdown 消息）。"""

from __future__ import annotations

import httpx

from app.notifiers.base import NotificationMessage, Notifier, SendResult

TIMEOUT_SECONDS = 10.0


def _safe_error(exc: Exception) -> str:
    """错误信息不携带 webhook URL，避免泄露机器人地址。"""
    return f"{type(exc).__name__}: 请求失败"


class WeComNotifier(Notifier):
    channel = "wecom"

    def __init__(self, webhook: str, timeout: float = TIMEOUT_SECONDS) -> None:
        self.webhook = webhook
        self.timeout = timeout

    def send(self, message: NotificationMessage) -> SendResult:
        content = f"**{message.title}**\n{message.body}" if message.body else message.text
        payload = {"msgtype": "markdown", "markdown": {"content": content}}
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

        if data.get("errcode") == 0:
            return SendResult(True)

        detail = str(data.get("errmsg") or "")[:200]
        return SendResult(False, f"errcode={data.get('errcode')} {detail}".strip())
