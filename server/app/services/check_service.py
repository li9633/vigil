"""管理端检测项（Check）用例：整表替换、校验、审计与 probe 目录缓存。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain.check_config import check_ids, validate_and_normalize_checks
from app.errors import NotFoundError, ValidationError
from app.logstore import log_event
from app.models import CheckConfig
from app.repositories import audit_repo, check_repo, client_repo, guard_repo
from app.schemas import iso_utc
from app.schemas.admin import CheckListResponse, ProbeCatalogResponse


def _ensure_client(db: Session, client_id: str) -> None:
    if client_repo.get_client(db, client_id) is None:
        raise NotFoundError("客户端不存在")


def _config_hash(config: dict[str, Any]) -> str:
    body = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


def _serialize_rows(rows: list[CheckConfig]) -> CheckListResponse:
    items: list[dict[str, Any]] = []
    for row in rows:
        try:
            config = json.loads(row.config_json or "{}")
        except json.JSONDecodeError:
            continue
        if isinstance(config, dict):
            items.append(config)
    return CheckListResponse(items=items, managed=bool(items))


def list_checks(db: Session, client_id: str) -> CheckListResponse:
    _ensure_client(db, client_id)
    return _serialize_rows(check_repo.list_rows(db, client_id))


def _ensure_guard_refs(
    db: Session, client_id: str, available: set[str]
) -> None:
    """替换后的 Check 集合必须覆盖现有 Guard 的全部 check_refs。"""
    for guard in guard_repo.load_guard_configs(db, client_id):
        for ref in guard.get("check_refs") or []:
            if ref not in available:
                raise ValidationError(
                    f"检测项被守护项引用，不能删除：{ref}"
                )


def replace_checks(
    db: Session, client_id: str, payload: dict[str, Any], *, actor: str
) -> CheckListResponse:
    """整体替换该客户端的 Check 配置。"""
    _ensure_client(db, client_id)

    raw_checks = payload.get("checks")
    if not isinstance(raw_checks, list):
        raise ValidationError("checks 必须是数组")

    configs = validate_and_normalize_checks(raw_checks)
    available = check_ids(configs)
    _ensure_guard_refs(db, client_id, available)

    now = utcnow()
    existing = check_repo.rows_map(db, client_id)
    for config in configs:
        cid = str(config["id"])
        row = existing.get(cid)
        if row is None:
            row = CheckConfig(client_id=client_id, check_id=cid)
            db.add(row)
        row.name = str(config["name"])
        row.probe = str(config["probe"])
        row.enabled = bool(config.get("enabled", True))
        row.config_json = json.dumps(config, ensure_ascii=False)
        row.config_hash = _config_hash(config)
        row.updated_at = now

    removed = 0
    for cid, row in existing.items():
        if cid not in available:
            db.delete(row)
            removed += 1

    audit_repo.add_audit(
        db,
        actor=actor,
        action="checks_update",
        target=client_id,
        ts=now,
        detail={
            "count": len(configs),
            "removed": removed,
            "check_ids": [str(config["id"]) for config in configs],
        },
    )
    db.commit()
    log_event(
        "info",
        "check_config.update",
        "检测项配置已更新",
        client_id=client_id,
        actor=actor,
        count=len(configs),
        removed=removed,
    )
    return _serialize_rows(check_repo.list_rows(db, client_id))


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
