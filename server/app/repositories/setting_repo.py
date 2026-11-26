"""键值配置（Setting 表）数据访问。"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import Setting


def get_raw(db: Session, key: str) -> str | None:
    row = db.get(Setting, key)
    if row is None:
        return None
    return row.value or ""


def get_json(db: Session, key: str) -> dict[str, Any] | None:
    """读取 JSON 对象；不存在、空值或格式非法时返回 None。"""
    raw = get_raw(db, key)
    if raw is None or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def set_json(db: Session, key: str, value: dict[str, Any]) -> None:
    row = db.get(Setting, key)
    if row is None:
        row = Setting(key=key, value="", updated_at=utcnow())
        db.add(row)
    row.value = json.dumps(value, ensure_ascii=False)
    row.updated_at = utcnow()
    db.flush()
