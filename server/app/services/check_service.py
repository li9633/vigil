"""管理端只读视图：客户端最近一次心跳上报的 probe 目录缓存。"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.repositories import client_repo
from app.schemas import iso_utc
from app.schemas.admin import ProbeCatalogResponse


def get_probe_catalog(db: Session, client_id: str) -> ProbeCatalogResponse:
    """最近一次心跳上报的 probe 目录（只读缓存）。"""
    client = client_repo.get_client(db, client_id)
    if client is None:
        raise NotFoundError("客户端不存在")

    try:
        raw = json.loads(client.probe_catalog_json or "[]")
    except json.JSONDecodeError:
        raw = []
    items = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    return ProbeCatalogResponse(
        items=items,
        updated_at=iso_utc(client.last_seen) if items else None,
    )
