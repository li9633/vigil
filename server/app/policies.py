"""运行时策略配置：阈值、抖动、动作与保留策略。

配置以 JSON 存于 `Setting(key='policies')`，未保存时回退环境变量默认值。
worker 与 API 每轮读取，保存后无需重启即可生效。
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import Setting
from app.settings import Settings

SETTING_KEY = "policies"

INT_BOUNDS: dict[str, tuple[int, int]] = {
    "heartbeat_interval": (10, 300),
    "suspect_seconds": (30, 3600),
    "down_seconds": (60, 7200),
    "beacon_seconds": (30, 3600),
    "flapping_window_seconds": (300, 86400),
    "flapping_threshold": (2, 20),
    "action_cooldown_seconds": (0, 3600),
    "action_max_per_hour": (1, 100),
    "retention_days": (0, 3650),
    "disk_alert_percent": (50, 99),
    "disk_alert_cooldown_seconds": (300, 86400),
}

BOOL_FIELDS = ("actions_enabled",)

POLICY_FIELDS = (*INT_BOUNDS, *BOOL_FIELDS)


def default_policies(settings: Settings) -> dict[str, Any]:
    return {name: getattr(settings, name) for name in POLICY_FIELDS}


def _coerce_int(name: str, value: Any, low: int, high: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须是整数")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 必须是整数") from None
    if not low <= number <= high:
        raise ValueError(f"{name} 需在 {low}-{high} 之间")
    return number


def _coerce_bool(name: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} 必须是布尔值")


def _merge_known(raw: Any, base: dict[str, Any]) -> dict[str, Any]:
    """只覆盖 payload 中出现的已知字段，避免部分更新把其他项重置为默认值。"""
    merged = dict(base)
    if not isinstance(raw, dict):
        return merged
    for name, (low, high) in INT_BOUNDS.items():
        if name in raw:
            merged[name] = _coerce_int(name, raw[name], low, high)
    for name in BOOL_FIELDS:
        if name in raw:
            merged[name] = _coerce_bool(name, raw[name])
    return merged


def normalize_policies(raw: Any, settings: Settings) -> dict[str, Any]:
    """在默认值上合并，只接受已知字段并做类型收敛。"""
    return _merge_known(raw, default_policies(settings))


def validate_policies(policies: dict[str, Any]) -> None:
    """跨字段约束：心跳 < SUSPECT < DOWN，beacon 不短于心跳。"""
    if policies["heartbeat_interval"] >= policies["suspect_seconds"]:
        raise ValueError("SUSPECT 阈值必须大于心跳间隔")
    if policies["suspect_seconds"] >= policies["down_seconds"]:
        raise ValueError("DOWN 阈值必须大于 SUSPECT 阈值")
    if policies["beacon_seconds"] < policies["heartbeat_interval"]:
        raise ValueError("beacon 阈值不能小于心跳间隔")


def _load_raw(db: Session) -> Any:
    row = db.get(Setting, SETTING_KEY)
    if row is None or not (row.value or "").strip():
        return None
    try:
        return json.loads(row.value)
    except json.JSONDecodeError:
        return None


def load_policies(db: Session, settings: Settings) -> dict[str, Any]:
    try:
        policies = normalize_policies(_load_raw(db), settings)
        validate_policies(policies)
    except ValueError:
        return default_policies(settings)
    return policies


def save_policies(
    db: Session, payload: dict[str, Any], settings: Settings
) -> dict[str, Any]:
    """部分更新：未提供的字段沿用当前值，校验通过后整体落库。"""
    current = _merge_known(payload, load_policies(db, settings))
    validate_policies(current)

    row = db.get(Setting, SETTING_KEY)
    if row is None:
        row = Setting(key=SETTING_KEY, value="", updated_at=utcnow())
        db.add(row)
    row.value = json.dumps(current, ensure_ascii=False)
    row.updated_at = utcnow()
    db.flush()
    return current


def apply_policies(settings: Settings, policies: dict[str, Any]) -> Settings:
    return replace(settings, **policies)


def effective_settings(db: Session, settings: Settings) -> Settings:
    """环境变量默认值叠加数据库策略，供运行时读取。"""
    return apply_policies(settings, load_policies(db, settings))
