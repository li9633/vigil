"""SMTP 邮件通知，支持 ssl / starttls / none 三种连接方式。"""

from __future__ import annotations

from collections.abc import Sequence
from email.message import EmailMessage

from app.adapters.smtp import TIMEOUT_SECONDS, SmtpClient, SmtplibClient, describe_smtp_error
from app.domain.notifications.ports import NotificationMessage, Notifier, SendResult


class EmailNotifier(Notifier):
    channel = "email"

    def __init__(
        self,
        host: str,
        port: int = 465,
        username: str = "",
        password: str = "",
        sender: str = "",
        recipients: Sequence[str] = (),
        security: str = "ssl",
        timeout: float = TIMEOUT_SECONDS,
        smtp_client: SmtpClient | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sender = sender
        self.recipients = [item for item in recipients if item]
        self.security = security if security in {"ssl", "starttls", "none"} else "ssl"
        self.timeout = timeout
        self.smtp_client = smtp_client or SmtplibClient()

    def _build_message(self, message: NotificationMessage) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = message.title
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        msg.set_content(message.body or message.title, charset="utf-8")
        return msg

    def send(self, message: NotificationMessage) -> SendResult:
        if not self.host:
            return SendResult(False, "SMTP 主机未配置")
        if not self.recipients:
            return SendResult(False, "收件人未配置")

        try:
            msg = self._build_message(message)
        except Exception as exc:  # noqa: BLE001
            return SendResult(False, f"{type(exc).__name__}: 邮件内容构建失败")

        try:
            self.smtp_client.send(
                host=self.host,
                port=self.port,
                security=self.security,
                username=self.username,
                password=self.password,
                message=msg,
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001 - 不输出密码/服务器细节
            return SendResult(False, describe_smtp_error(exc))

        return SendResult(True)
