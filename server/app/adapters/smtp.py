"""SMTP 客户端接口与 smtplib 实现。

把连接细节独立成端口，邮件通知适配器只依赖接口，单测可直接注入假实现。
"""

from __future__ import annotations

import smtplib
from abc import ABC, abstractmethod
from email.message import EmailMessage

TIMEOUT_SECONDS = 10.0
SECURITY_MODES = ("ssl", "starttls", "none")


class SmtpClient(ABC):
    """邮件投递端口：失败时抛出异常，由调用方转换为发送结果。"""

    @abstractmethod
    def send(
        self,
        *,
        host: str,
        port: int,
        security: str,
        username: str,
        password: str,
        message: EmailMessage,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        """投递一封邮件。"""


class SmtplibClient(SmtpClient):
    """基于 smtplib 的默认实现，支持 ssl / starttls / none。"""

    def send(
        self,
        *,
        host: str,
        port: int,
        security: str,
        username: str,
        password: str,
        message: EmailMessage,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        if security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=timeout)
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)

        with server:
            if security == "starttls":
                server.starttls()
            if username:
                server.login(username, password)
            server.send_message(message)


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


def describe_smtp_error(exc: Exception) -> str:
    """把 SMTP 异常转为可读错误，避免把密码等本地信息写进通知记录。"""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return f"SMTP 认证失败（{_smtp_detail(exc)}）：请检查用户名与授权码"
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return f"发件人被拒绝（{_smtp_detail(exc)}）：发件人需与登录账号一致"
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return f"收件人被拒绝（{_smtp_detail(exc)}）"
    if isinstance(exc, smtplib.SMTPConnectError):
        return f"SMTP 连接失败（{_smtp_detail(exc)}）"
    return f"{type(exc).__name__}: 邮件发送失败"
