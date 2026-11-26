"""通知领域端口：消息结构、发送结果与渠道接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NotificationMessage:
    title: str
    body: str
    level: str = "info"
    incident_id: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """标题 + 正文的纯文本形式，供 webhook / 邮件正文使用。"""
        return f"{self.title}\n{self.body}" if self.body else self.title


@dataclass(frozen=True)
class SendResult:
    ok: bool
    error: str = ""


class Notifier(ABC):
    """通知渠道统一接口。"""

    channel: str = "base"

    @abstractmethod
    def send(self, message: NotificationMessage) -> SendResult:
        """发送通知，返回发送结果（不抛异常）。"""


class NullNotifier(Notifier):
    """空实现：直接返回成功，不产生任何外部调用。"""

    channel = "null"

    def send(self, message: NotificationMessage) -> SendResult:
        return SendResult(ok=True)
