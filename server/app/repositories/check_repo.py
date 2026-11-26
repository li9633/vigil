"""服务端下发的检测项配置。"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CheckConfig


def list_rows(db: Session, client_id: str) -> list[CheckConfig]:
    """读取该客户端的 Check 记录，按 check_id 排序。"""
    return list(
        db.scalars(
            select(CheckConfig)
            .where(CheckConfig.client_id == client_id)
            .order_by(CheckConfig.check_id)
        ).all()
    )


def rows_map(db: Session, client_id: str) -> dict[str, CheckConfig]:
    return {row.check_id: row for row in list_rows(db, client_id)}


def load_check_configs(db: Session, client_id: str) -> list[dict[str, Any]]:
    """解析并返回该客户端下发的 Check 配置（按 check_id 排序）。"""
    items: list[dict[str, Any]] = []
    for row in list_rows(db, client_id):
        try:
            config = json.loads(row.config_json or "{}")
        except json.JSONDecodeError:
            continue
        if isinstance(config, dict):
            items.append(config)
    return items
