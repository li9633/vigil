"""SMTP 邮件通知，支持 ssl / starttls / none 三种连接方式。"""

from __future__ import annotations

import smtplib
from collections.abc import Sequence
from email.message import EmailMessage

from app.notifiers.base import NotificationMessage, Notifier, SendResult

TIMEOUT_SECONDS = 10.0


def _smtp_detail(exc: smtplib.SMTPException) -> str:
    """提取服务端返回码与说明，便于定位配置问题；不包含本地凭据。"""
    code = getattr(exc, "smtp_code", None)
    error = getattr(exc, "smtp_error", None)
    if isinstance(error, bytes):
        error = error.decode("utf-8", "replace")
    parts = [str(code)] if code else []
    if error:
        parts.append(str(error).strip())
    return " ".join(parts)


def _describe(exc: Exception) -> str:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return f"SMTP 认证失败（{_smtp_detail(exc)}）：请检查用户名与授权码"
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return f"发件人被拒绝（{_smtp_detail(exc)}）：发件人需与登录账号一致"
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return f"收件人被拒绝（{_smtp_detail(exc)}）"
    if isinstance(exc, smtplib.SMTPConnectError):
        return f"SMTP 连接失败（{_smtp_detail(exc)}）"
    return f"{type(exc).__name__}: 邮件发送失败"


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
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sender = sender
        self.recipients = [item for item in recipients if item]
        self.security = security if security in {"ssl", "starttls", "none"} else "ssl"
        self.timeout = timeout

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
            if self.security == "ssl":
                server: smtplib.SMTP = smtplib.SMTP_SSL(
                    self.host, self.port, timeout=self.timeout
                )
            else:
                server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)

            with server:
                if self.security == "starttls":
                    server.starttls()
                if self.username:
                    server.login(self.username, self.password)
                server.send_message(msg)
        except Exception as exc:  # noqa: BLE001 - 不输出密码/服务器细节
            return SendResult(False, _describe(exc))

        return SendResult(True)
