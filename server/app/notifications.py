"""兼容导出：通知能力已拆分到领域层、适配器与服务层。

新代码请直接 import 对应模块：
- 配置规则：`app.domain.notifications.config`
- 消息模板：`app.domain.notifications.templates`
- 渠道适配器：`app.adapters.notifiers`
- 发送用例：`app.services.notification_service`
"""

from __future__ import annotations

from app.adapters.notifiers.base import NotificationMessage, Notifier, SendResult
from app.adapters.notifiers.factory import build_notifiers, target_for
from app.domain.notifications.config import (
    CHANNEL_LABELS,
    DEFAULT_CONFIG,
    MASK_PREFIX,
    SECURITY_MODES,
    SETTING_KEY,
    apply_masked,
    config_from_env,
    default_config,
    mask_config,
    mask_secret,
    normalize_config,
    validate_config,
)
from app.domain.notifications.templates import (
    GUARD_BAD_STATUSES,
    MAX_ACTIONS,
    MAX_SNIPPET,
    build_flapping_message,
    build_message,
)
from app.services.notification_service import (
    DEAD_LETTER_KINDS,
    FLAPPING_SUMMARY_KEY,
    KIND_FLAPPING,
    KIND_SYSTEM,
    MAX_DEAD_ALERT_ITEMS,
    MAX_RETRY_ATTEMPTS,
    RETRY_BASE_SECONDS,
    SYSTEM_CLIENT_ID,
    load_config,
    pending_recovery_incidents,
    retry_failed,
    save_config,
    send_incident,
    send_system_alert,
)
from app.services.serializers import notification_item as serialize_notification

__all__ = [
    "CHANNEL_LABELS",
    "DEFAULT_CONFIG",
    "DEAD_LETTER_KINDS",
    "FLAPPING_SUMMARY_KEY",
    "GUARD_BAD_STATUSES",
    "KIND_FLAPPING",
    "KIND_SYSTEM",
    "MASK_PREFIX",
    "MAX_ACTIONS",
    "MAX_DEAD_ALERT_ITEMS",
    "MAX_RETRY_ATTEMPTS",
    "MAX_SNIPPET",
    "NotificationMessage",
    "Notifier",
    "RETRY_BASE_SECONDS",
    "SECURITY_MODES",
    "SETTING_KEY",
    "SYSTEM_CLIENT_ID",
    "SendResult",
    "apply_masked",
    "build_flapping_message",
    "build_message",
    "build_notifiers",
    "config_from_env",
    "default_config",
    "load_config",
    "mask_config",
    "mask_secret",
    "normalize_config",
    "pending_recovery_incidents",
    "retry_failed",
    "save_config",
    "send_incident",
    "send_system_alert",
    "serialize_notification",
    "target_for",
    "validate_config",
]
