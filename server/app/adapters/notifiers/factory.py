"""根据通知配置构建已启用的适配器列表；未配置的渠道直接跳过。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

from app.adapters.notifiers.email import EmailNotifier
from app.adapters.notifiers.feishu import FeishuNotifier
from app.adapters.notifiers.wecom import WeComNotifier
from app.domain.notifications.config import CHANNELS
from app.domain.notifications.ports import Notifier


def parse_recipients(value: str | Iterable[str] | None) -> list[str]:
    """`to` 支持逗号/分号分隔，也兼容数组。"""
    if isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        items = re.split(r"[,;]", str(value or ""))
    return [item.strip() for item in items if item and item.strip()]


def webhook_target(webhook: str) -> str:
    """记录用 target：只保留主机名，避免把带 token 的 webhook 落库。"""
    host = urlparse(str(webhook or "")).netloc
    return host or "webhook"


def target_for(channel: str, config: dict[str, Any]) -> str:
    section = config.get(channel) or {}
    if channel == "email":
        return ", ".join(parse_recipients(section.get("to")))
    return webhook_target(str(section.get("webhook") or ""))


def build_notifier(channel: str, config: dict[str, Any]) -> Notifier | None:
    """构建单个渠道；未启用或关键参数缺失时返回 None。"""
    section = config.get(channel) or {}
    if not section.get("enabled"):
        return None

    if channel in ("feishu", "wecom"):
        webhook = str(section.get("webhook") or "").strip()
        if not webhook:
            return None
        if channel == "feishu":
            return FeishuNotifier(webhook)
        return WeComNotifier(webhook)

    if channel == "email":
        host = str(section.get("smtp_host") or "").strip()
        recipients = parse_recipients(section.get("to"))
        if not host or not recipients:
            return None
        try:
            port = int(section.get("smtp_port") or 465)
        except (TypeError, ValueError):
            port = 465
        return EmailNotifier(
            host=host,
            port=port,
            username=str(section.get("username") or ""),
            password=str(section.get("password") or ""),
            sender=str(section.get("from") or ""),
            recipients=recipients,
            security=str(section.get("security") or "ssl"),
        )

    return None


def build_notifiers(config: dict[str, Any]) -> list[Notifier]:
    notifiers: list[Notifier] = []
    for channel in CHANNELS:
        notifier = build_notifier(channel, config)
        if notifier is not None:
            notifiers.append(notifier)
    return notifiers
