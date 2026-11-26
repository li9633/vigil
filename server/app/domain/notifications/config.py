"""通知配置规则：默认值、环境变量回退、规范化、掩码与校验。

本模块不接触数据库；持久化由 `services.notification_service` 通过
`repositories.setting_repo` 完成。
"""

from __future__ import annotations

import copy
import os
from typing import Any

SETTING_KEY = "notifications"
MASK_PREFIX = "****"
SECURITY_MODES = ("ssl", "starttls", "none")
CHANNELS = ("feishu", "wecom", "email")

DEFAULT_CONFIG: dict[str, Any] = {
    "feishu": {"enabled": False, "webhook": ""},
    "wecom": {"enabled": False, "webhook": ""},
    "email": {
        "enabled": False,
        "smtp_host": "",
        "smtp_port": 465,
        "username": "",
        "password": "",
        "from": "",
        "to": "",
        "security": "ssl",
    },
    "rate_limit_per_hour": 10,
}

CHANNEL_LABELS = {"feishu": "飞书", "wecom": "企业微信", "email": "邮件"}


def default_config() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_CONFIG)


def _env(name: str) -> str:
    value = os.getenv(name)
    return "" if value is None else value.strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


def config_from_env() -> dict[str, Any]:
    """未保存配置时的回退值：仅填写存在环境变量的字段。"""
    config = default_config()

    feishu_webhook = _env("VIGIL_FEISHU_WEBHOOK")
    if feishu_webhook:
        config["feishu"] = {"enabled": True, "webhook": feishu_webhook}

    wecom_webhook = _env("VIGIL_WECOM_WEBHOOK")
    if wecom_webhook:
        config["wecom"] = {"enabled": True, "webhook": wecom_webhook}

    host = _env("VIGIL_SMTP_HOST")
    sender = _env("VIGIL_SMTP_FROM")
    recipients = _env("VIGIL_SMTP_TO")
    security = _env("VIGIL_SMTP_SECURITY") or "ssl"
    if security not in SECURITY_MODES:
        security = "ssl"
    config["email"] = {
        "enabled": bool(host and sender and recipients),
        "smtp_host": host,
        "smtp_port": _env_int("VIGIL_SMTP_PORT", 465),
        "username": _env("VIGIL_SMTP_USERNAME"),
        "password": _env("VIGIL_SMTP_PASSWORD"),
        "from": sender,
        "to": recipients,
        "security": security,
    }
    return config


def _normalize_to(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ",".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def normalize_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """在默认结构上合并，忽略未知字段并做类型收敛。"""
    config = default_config()
    if not isinstance(raw, dict):
        return config

    for channel in ("feishu", "wecom"):
        section = raw.get(channel)
        if isinstance(section, dict):
            config[channel]["enabled"] = bool(section.get("enabled", False))
            config[channel]["webhook"] = str(section.get("webhook") or "").strip()

    email = raw.get("email")
    if isinstance(email, dict):
        target = config["email"]
        target["enabled"] = bool(email.get("enabled", False))
        target["smtp_host"] = str(email.get("smtp_host") or "").strip()
        try:
            target["smtp_port"] = int(email.get("smtp_port") or 465)
        except (TypeError, ValueError):
            target["smtp_port"] = 465
        target["username"] = str(email.get("username") or "").strip()
        target["password"] = str(email.get("password") or "")
        target["from"] = str(email.get("from") or "").strip()
        target["to"] = _normalize_to(email.get("to"))
        security = str(email.get("security") or "ssl").strip()
        target["security"] = security if security in SECURITY_MODES else "ssl"

    try:
        rate_limit = int(raw.get("rate_limit_per_hour", config["rate_limit_per_hour"]))
    except (TypeError, ValueError):
        rate_limit = config["rate_limit_per_hour"]
    config["rate_limit_per_hour"] = (
        rate_limit if rate_limit >= 0 else config["rate_limit_per_hour"]
    )
    return config


def apply_masked(payload: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """以 **** 开头的值表示沿用旧值（前端只展示掩码）。"""
    merged = normalize_config(payload)

    def keep(section_name: str, field: str) -> None:
        incoming = merged[section_name].get(field, "")
        previous = current.get(section_name, {}).get(field, "")
        if isinstance(incoming, str) and incoming.startswith(MASK_PREFIX):
            merged[section_name][field] = previous

    keep("feishu", "webhook")
    keep("wecom", "webhook")
    keep("email", "password")
    return merged


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    text = str(value)
    return f"{MASK_PREFIX}{text[-4:]}"


def mask_config(config: dict[str, Any]) -> dict[str, Any]:
    masked = normalize_config(config)
    masked["feishu"]["webhook"] = mask_secret(masked["feishu"]["webhook"])
    masked["wecom"]["webhook"] = mask_secret(masked["wecom"]["webhook"])
    masked["email"]["password"] = mask_secret(masked["email"]["password"])
    return masked


def _is_http_url(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def validate_config(config: dict[str, Any]) -> None:
    """校验保存/测试前的配置，失败抛 ValueError（由服务层转领域异常）。"""
    for channel, label in (("feishu", "飞书"), ("wecom", "企业微信")):
        section = config.get(channel) or {}
        webhook = str(section.get("webhook") or "").strip()
        if section.get("enabled") and not webhook:
            raise ValueError(f"{label} Webhook 不能为空")
        if webhook and not _is_http_url(webhook):
            raise ValueError(f"{label} Webhook 必须以 http:// 或 https:// 开头")

    email = config.get("email") or {}
    if email.get("enabled"):
        for field, message in (
            ("smtp_host", "SMTP 主机不能为空"),
            ("from", "发件人不能为空"),
            ("to", "收件人不能为空"),
        ):
            if not str(email.get(field) or "").strip():
                raise ValueError(f"邮件已启用：{message}")
        port = email.get("smtp_port")
        if port is None:
            raise ValueError("SMTP 端口必须是整数")
        try:
            port_number = int(port)
        except (TypeError, ValueError):
            raise ValueError("SMTP 端口必须是整数") from None
        if not 1 <= port_number <= 65535:
            raise ValueError("SMTP 端口需在 1-65535 之间")
    if str(email.get("security") or "ssl") not in SECURITY_MODES:
        raise ValueError("邮件安全模式仅支持 ssl / starttls / none")

    rate_limit = config.get("rate_limit_per_hour")
    if rate_limit is None:
        raise ValueError("每小时通知上限必须是整数")
    try:
        rate_number = int(rate_limit)
    except (TypeError, ValueError):
        raise ValueError("每小时通知上限必须是整数") from None
    if rate_number < 0:
        raise ValueError("每小时通知上限不能为负数")
