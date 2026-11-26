"""管理端守护项（Guard）配置用例：整体读写、校验与审计。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain.check_config import check_ids, validate_and_normalize_guards
from app.errors import NotFoundError, ValidationError
from app.logstore import log_event
from app.models import GuardConfig
from app.repositories import audit_repo, check_repo, client_repo, guard_repo
from app.schemas.admin import GuardListResponse


def _ensure_client(db: Session, client_id: str) -> None:
    if client_repo.get_client(db, client_id) is None:
        raise NotFoundError("客户端不存在")


def _guard_config_hash(config: dict[str, Any]) -> str:
    body = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


def _serialize_rows(rows: list[GuardConfig]) -> GuardListResponse:
    """按 guard_id 升序返回配置对象；只暴露配置本身，不含存储元数据。"""
    items: list[dict[str, Any]] = []
    for row in rows:
        try:
            config = json.loads(row.config_json or "{}")
        except json.JSONDecodeError:
            continue
        if isinstance(config, dict):
            items.append(config)
    return GuardListResponse(items=items, managed=bool(items))


def list_guards(db: Session, client_id: str) -> GuardListResponse:
    _ensure_client(db, client_id)
    return _serialize_rows(guard_repo.list_rows(db, client_id))


def replace_guards(
    db: Session, client_id: str, payload: dict[str, Any], *, actor: str
) -> GuardListResponse:
    """整体替换该客户端的 Guard 配置，check_refs 必须指向已保存的检测项。"""
    _ensure_client(db, client_id)

    raw_guards = payload.get("guards")
    if not isinstance(raw_guards, list):
        raise ValidationError("guards 必须是数组")

    available = check_ids(check_repo.load_check_configs(db, client_id))
    configs = validate_and_normalize_guards(raw_guards, available)

    now = utcnow()
    existing = guard_repo.rows_map(db, client_id)
    for config in configs:
        guard_id = str(config["id"])
        row = existing.get(guard_id)
        if row is None:
            row = GuardConfig(client_id=client_id, guard_id=guard_id)
            db.add(row)
        row.name = str(config["name"])
        row.template = str(config.get("template") or "")
        row.enabled = bool(config.get("enabled", True))
        row.config_json = json.dumps(config, ensure_ascii=False)
        row.config_hash = _guard_config_hash(config)
        row.updated_at = now

    removed = 0
    for guard_id, row in existing.items():
        if guard_id not in {str(config["id"]) for config in configs}:
            db.delete(row)
            removed += 1

    audit_repo.add_audit(
        db,
        actor=actor,
        action="guards_update",
        target=client_id,
        ts=now,
        detail={
            "count": len(configs),
            "removed": removed,
            "guard_ids": [str(config["id"]) for config in configs],
        },
    )
    db.commit()
    log_event(
        "info",
        "guard_config.update",
        "守护项配置已更新",
        client_id=client_id,
        actor=actor,
        count=len(configs),
        removed=removed,
    )
    return _serialize_rows(guard_repo.list_rows(db, client_id))


def clear_guards(db: Session, client_id: str, *, actor: str) -> GuardListResponse:
    _ensure_client(db, client_id)

    removed = guard_repo.list_rows(db, client_id)
    for row in removed:
        db.delete(row)

    audit_repo.add_audit(
        db,
        actor=actor,
        action="guards_clear",
        target=client_id,
        ts=utcnow(),
        detail={"removed": len(removed)},
    )
    db.commit()
    log_event(
        "info",
        "guard_config.clear",
        "守护项下发配置已清除",
        client_id=client_id,
        actor=actor,
        removed=len(removed),
    )
    return GuardListResponse(items=[], managed=False)
