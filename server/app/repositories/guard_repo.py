"""服务端下发的守护项配置。"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GuardConfig


def load_guard_configs(db: Session, client_id: str) -> list[dict[str, Any]]:
    """读取该客户端配置的 Guard，按 guard_id 排序。"""
    rows = db.scalars(
        select(GuardConfig)
        .where(GuardConfig.client_id == client_id)
        .order_by(GuardConfig.guard_id)
    ).all()
    items: list[dict[str, Any]] = []
    for row in rows:
        try:
            config = json.loads(row.config_json or "{}")
        except json.JSONDecodeError:
            continue
        if isinstance(config, dict):
            items.append(config)
    return items
