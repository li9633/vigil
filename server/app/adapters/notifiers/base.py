"""通知端口兼容导出：消息结构、发送结果与渠道接口均定义在领域层。"""

from __future__ import annotations

from app.domain.notifications.ports import (
    NotificationMessage,
    Notifier,
    NullNotifier,
    SendResult,
)

__all__ = ["NotificationMessage", "Notifier", "NullNotifier", "SendResult"]
