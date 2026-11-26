"""服务端下发的守护项配置。"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GuardConfig


def list_rows(db: Session, client_id: str) -> list[GuardConfig]:
    """读取该客户端的 Guard 记录，按 guard_id 排序。"""
    return list(
        db.scalars(
            select(GuardConfig)
            .where(GuardConfig.client_id == client_id)
            .order_by(GuardConfig.guard_id)
        ).all()
    )


def rows_map(db: Session, client_id: str) -> dict[str, GuardConfig]:
    return {row.guard_id: row for row in list_rows(db, client_id)}


def load_guard_configs(db: Session, client_id: str) -> list[dict[str, Any]]:
    """解析并返回该客户端下发的 Guard 配置（按 guard_id 排序）。"""
    items: list[dict[str, Any]] = []
    for row in list_rows(db, client_id):
        try:
            config = json.loads(row.config_json or "{}")
        except json.JSONDecodeError:
            continue
        if isinstance(config, dict):
            items.append(config)
    return items
