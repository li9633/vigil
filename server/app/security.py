"""客户端鉴权：secret 哈希、时间窗、nonce 防重放。"""

from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import ForbiddenError, UnauthorizedError
from app.models import Client, Nonce
from app.settings import Settings


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_client_id() -> str:
    return "cl_" + secrets.token_hex(8)


def new_client_secret() -> str:
    """生成随机 secret，仅在注册响应中返回一次。"""
    return secrets.token_urlsafe(32)


def hash_secret(secret: str) -> str:
    return sha256_hex(secret)


def constant_time_equals(left: str, right: str) -> bool:
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def verify_bootstrap(provided: str | None, expected: str) -> None:
    if not provided or not constant_time_equals(provided, expected):
        raise UnauthorizedError("bootstrap token 无效")


def parse_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError(
            "缺少 Bearer 凭证", headers={"WWW-Authenticate": "Bearer"}
        )
    secret = authorization[7:].strip()
    if not secret:
        raise UnauthorizedError(
            "缺少 Bearer 凭证", headers={"WWW-Authenticate": "Bearer"}
        )
    return secret


def _parse_timestamp(raw: str | None) -> int:
    try:
        return int(raw or "")
    except (TypeError, ValueError):
        raise UnauthorizedError("时间戳无效") from None


def consume_nonce(db: Session, nonce: str | None, client_id: str, now: datetime) -> None:
    """持久化 nonce；重复出现视为重放。"""
    if not nonce:
        raise UnauthorizedError("缺少 nonce")
    db.add(Nonce(nonce=nonce, client_id=client_id, seen_at=now))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise UnauthorizedError("nonce 重放") from None


def purge_expired_nonces(db: Session, now: datetime, ttl_seconds: int) -> int:
    """清理过期 nonce，返回删除条数。"""
    cutoff = now - timedelta(seconds=ttl_seconds)
    result = db.execute(delete(Nonce).where(Nonce.seen_at < cutoff))
    return int(getattr(result, "rowcount", 0) or 0)


def authenticate_client(
    db: Session,
    settings: Settings,
    *,
    client_id: str | None,
    authorization: str | None,
    timestamp: str | None,
    nonce: str | None,
    now: datetime,
) -> Client:
    """校验收包头：客户端 ID、secret 哈希、时间窗、nonce。"""
    if not client_id:
        raise UnauthorizedError("缺少 X-Vigil-Client")
    request_ts = _parse_timestamp(timestamp)
    if abs(time.time() - request_ts) > settings.auth_window_seconds:
        raise UnauthorizedError("时间戳超出允许窗口")

    client = db.scalars(select(Client).where(Client.client_id == client_id)).one_or_none()
    secret = parse_bearer(authorization)
    if client is None or not constant_time_equals(hash_secret(secret), client.secret_sha256):
        raise UnauthorizedError(
            "客户端凭证无效", headers={"WWW-Authenticate": "Bearer"}
        )
    if not client.enabled:
        raise ForbiddenError("客户端已被禁用")

    consume_nonce(db, nonce, client.client_id, now)
    return client
