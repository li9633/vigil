"""管理端客户端写操作：彻底删除客户端及其关联数据。"""

from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.logstore import log_event
from app.models import (
    Beacon,
    CheckConfig,
    ClientEvent,
    GuardConfig,
    Heartbeat,
    Incident,
    Nonce,
    Notification,
)
from app.repositories import audit_repo, client_repo

CHILD_MODELS = (
    Beacon,
    Heartbeat,
    CheckConfig,
    GuardConfig,
    ClientEvent,
    Incident,
    Notification,
    Nonce,
)


def delete_client(db: Session, client_id: str, *, actor: str) -> dict:
    client = client_repo.get_client(db, client_id)
    if client is None:
        raise NotFoundError("客户端不存在")

    detail = {"name": client.name, "model": client.model}
    for model in CHILD_MODELS:
        db.execute(delete(model).where(model.client_id == client_id))
    audit_repo.add_audit(
        db,
        actor=actor,
        action="client.delete",
        target=client_id,
        detail=detail,
    )
    db.delete(client)
    db.commit()
    log_event(
        "warn",
        "client.delete",
        "客户端已被彻底删除",
        client_id=client_id,
        actor=actor,
        name=detail["name"],
    )
    return {"client_id": client_id, "deleted": True}
