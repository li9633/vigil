"""客户端数据访问。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Client


def get_client(db: Session, client_id: str) -> Client | None:
    return db.scalars(select(Client).where(Client.client_id == client_id)).one_or_none()
