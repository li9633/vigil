"""配对流程用例：客户端发起、管理员确认、凭 claim_token 领取凭证。

安全约束：
- 配对码只存 HMAC，且短 TTL、失败次数受限；
- 领取凭证 claim_token 只存 SHA-256，必须由创建请求的客户端持有；
- 客户端 secret 由 claim_token 派生，服务端不保存明文（只存哈希）。
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.db import utcnow
from app.errors import (
    ConflictError,
    NotFoundError,
    RateLimitedError,
    UnauthorizedError,
    ValidationError,
)
from app.logstore import log_event
from app.models import Client, PairingRequest
from app.policies import effective_settings
from app.repositories import audit_repo
from app.schemas import PairingStartRequest
from app.security import (
    constant_time_equals,
    derive_client_secret,
    hash_claim_token,
    hash_pairing_code,
    hash_secret,
    new_claim_token,
    new_client_id,
    new_pairing_request_id,
    normalize_pairing_code,
)
from app.settings import Settings
from app.utils.time import iso_utc

PENDING = "pending"
APPROVED = "approved"
CLAIMED = "claimed"
DENIED = "denied"
EXPIRED = "expired"
ACTIVE_STATUSES = (PENDING, APPROVED)


def _pepper(settings: Settings) -> str:
    return settings.pairing_pepper or settings.session_secret


def _epoch(value: datetime | None) -> int | None:
    """SQLite 存的是 naive UTC，转 epoch 时必须按 UTC 解释。"""
    return None if value is None else int(value.replace(tzinfo=UTC).timestamp())


def _expire_row(db: Session, row: PairingRequest, now: datetime) -> None:
    row.status = EXPIRED
    row.denied_at = now
    row.denied_reason = row.denied_reason or "expired"


def _as_item(row: PairingRequest) -> dict:
    return {
        "request_id": row.request_id,
        "instance_id": row.instance_id,
        "name": row.name,
        "model": row.model,
        "fw_version": row.fw_version,
        "agent_version": row.agent_version,
        "client_ip": row.client_ip,
        "status": row.status,
        "attempts": row.attempts,
        "client_id": row.client_id,
        "created_at": iso_utc(row.created_at),
        "expires_at": iso_utc(row.expires_at),
        "code_expires_at": iso_utc(row.code_expires_at),
        "approved_at": iso_utc(row.approved_at),
        "approved_by": row.approved_by,
        "claimed_at": iso_utc(row.claimed_at),
        "denied_at": iso_utc(row.denied_at),
        "denied_reason": row.denied_reason,
    }


def _find_active(db: Session, request_id: str) -> PairingRequest:
    row = db.scalar(select(PairingRequest).where(PairingRequest.request_id == request_id))
    if row is None:
        raise NotFoundError("配对请求不存在")
    return row


def _check_rate_limit(db: Session, settings: Settings, client_ip: str, now: datetime) -> None:
    window = timedelta(seconds=settings.pairing_rate_window_seconds)
    since = now - window
    count = db.scalar(
        select(func.count())
        .select_from(PairingRequest)
        .where(PairingRequest.client_ip == client_ip, PairingRequest.created_at >= since)
    )
    if (count or 0) < settings.pairing_rate_limit:
        return
    oldest = db.scalar(
        select(PairingRequest.created_at)
        .where(PairingRequest.client_ip == client_ip, PairingRequest.created_at >= since)
        .order_by(PairingRequest.created_at)
        .limit(1)
    )
    retry_after = settings.pairing_rate_window_seconds
    if oldest is not None:
        retry_after = max(1, int((oldest + window - now).total_seconds()))
    raise RateLimitedError("配对请求过于频繁，请稍后再试", retry_after=retry_after)


def start_pairing(
    db: Session,
    settings: Settings,
    payload: PairingStartRequest,
    *,
    client_ip: str,
) -> dict:
    code = normalize_pairing_code(payload.code)
    if not code:
        raise ValidationError("配对码格式不正确")
    if payload.protocol != 1:
        raise ValidationError("不支持的协议版本")

    now = utcnow()
    _check_rate_limit(db, settings, client_ip, now)

    # 同一实例重新发起时，旧请求直接作废，避免待审批列表堆积
    db.execute(
        update(PairingRequest)
        .where(
            PairingRequest.instance_id == payload.instance_id,
            PairingRequest.status == PENDING,
        )
        .values(status=EXPIRED, denied_at=now, denied_reason="superseded")
    )

    request_id = new_pairing_request_id()
    claim_token = new_claim_token()
    row = PairingRequest(
        request_id=request_id,
        instance_id=payload.instance_id,
        name=payload.name,
        model=payload.model,
        fw_version=payload.fw_version,
        agent_version=payload.agent_version,
        client_ip=client_ip,
        code_hmac=hash_pairing_code(code, _pepper(settings), request_id),
        code_expires_at=now + timedelta(seconds=settings.pairing_code_ttl_seconds),
        claim_sha256=hash_claim_token(claim_token),
        status=PENDING,
        created_at=now,
        expires_at=now + timedelta(seconds=settings.pairing_request_ttl_seconds),
    )
    db.add(row)
    db.commit()
    log_event(
        "info",
        "pairing.request",
        "收到客户端配对请求",
        request_id=request_id,
        name=payload.name,
        model=payload.model,
        client_ip=client_ip,
    )
    return {
        "request_id": request_id,
        "claim_token": claim_token,
        "server_time": int(time.time()),
        "expires_at": _epoch(row.expires_at),
        "code_expires_at": _epoch(row.code_expires_at),
    }


def pairing_status(
    db: Session,
    settings: Settings,
    *,
    request_id: str,
    claim_token: str,
) -> dict:
    row = db.scalar(select(PairingRequest).where(PairingRequest.request_id == request_id))
    if row is None or not constant_time_equals(hash_claim_token(claim_token), row.claim_sha256):
        raise UnauthorizedError("配对凭证无效")

    now = utcnow()
    if row.status in ACTIVE_STATUSES and row.expires_at <= now:
        _expire_row(db, row, now)
        db.commit()
        log_event("info", "pairing.expire", "配对请求已过期", request_id=request_id)

    if row.status == PENDING:
        return {
            "status": PENDING,
            "server_time": int(time.time()),
            "expires_at": _epoch(row.expires_at),
            "code_expires_at": _epoch(row.code_expires_at),
        }
    if row.status == DENIED:
        return {
            "status": DENIED,
            "server_time": int(time.time()),
            "reason": row.denied_reason or "denied",
        }
    if row.status == EXPIRED:
        return {
            "status": EXPIRED,
            "server_time": int(time.time()),
            "reason": row.denied_reason or "expired",
        }

    # approved / claimed：claim_token 可重复领取同一 secret，避免响应丢失导致客户端卡死
    secret = derive_client_secret(claim_token, _pepper(settings))
    client = db.scalar(select(Client).where(Client.client_id == row.client_id))
    if client is None:
        client = Client(
            client_id=row.client_id,
            name=row.name,
            secret_sha256=hash_secret(secret),
            model=row.model,
            fw_version=row.fw_version,
            agent_version=row.agent_version,
        )
        db.add(client)
    elif not constant_time_equals(client.secret_sha256, hash_secret(secret)):
        raise UnauthorizedError("客户端凭证冲突，请重新发起配对")

    first_claim = row.status == APPROVED
    if first_claim:
        row.status = CLAIMED
        row.claimed_at = now
        log_event(
            "info",
            "pairing.claim",
            "客户端领取配对凭证",
            request_id=request_id,
            client_id=row.client_id,
        )
    db.commit()
    return {
        "status": APPROVED,
        "server_time": int(time.time()),
        "client_id": row.client_id,
        "secret": secret,
        "heartbeat_interval": effective_settings(db, settings).heartbeat_interval,
        "expires_at": _epoch(row.expires_at),
    }


def approve_pairing(
    db: Session,
    settings: Settings,
    *,
    request_id: str,
    code: str,
    actor: str,
) -> dict:
    row = _find_active(db, request_id)
    now = utcnow()
    if row.status != PENDING:
        raise ConflictError("配对请求已被处理")
    if row.expires_at <= now:
        _expire_row(db, row, now)
        db.commit()
        raise ConflictError("配对请求已过期")
    if row.code_expires_at <= now:
        raise ValidationError("配对码已过期，请在客户端重新生成")
    if row.attempts >= settings.pairing_code_max_attempts:
        row.status = DENIED
        row.denied_at = now
        row.denied_reason = "too_many_attempts"
        row.code_hmac = ""
        db.commit()
        raise ConflictError("配对码尝试次数过多，请重新发起配对")

    normalized = normalize_pairing_code(code)
    provided = hash_pairing_code(normalized, _pepper(settings), row.request_id) if normalized else ""
    if not normalized or not constant_time_equals(provided, row.code_hmac):
        row.attempts += 1
        remaining = max(0, settings.pairing_code_max_attempts - row.attempts)
        if remaining == 0:
            row.status = DENIED
            row.denied_at = now
            row.denied_reason = "too_many_attempts"
            row.code_hmac = ""
        db.commit()
        log_event(
            "warn",
            "pairing.approve.fail",
            "配对码校验失败",
            request_id=request_id,
            actor=actor,
            remaining=remaining,
        )
        if remaining == 0:
            raise ConflictError("配对码尝试次数过多，请重新发起配对")
        raise ValidationError("配对码不正确，剩余 %d 次" % remaining)

    row.status = APPROVED
    row.approved_at = now
    row.approved_by = actor
    row.client_id = new_client_id()
    row.code_hmac = ""  # 通过后立即销毁配对码
    audit_repo.add_audit(
        db,
        actor=actor,
        action="pairing.approve",
        target=request_id,
        detail={"name": row.name, "client_id": row.client_id},
    )
    db.commit()
    log_event(
        "info",
        "pairing.approve",
        "配对请求已批准",
        request_id=request_id,
        actor=actor,
        name=row.name,
        client_id=row.client_id,
    )
    return _as_item(row)


def deny_pairing(
    db: Session,
    settings: Settings,
    *,
    request_id: str,
    reason: str,
    actor: str,
) -> dict:
    row = _find_active(db, request_id)
    now = utcnow()
    if row.status != PENDING:
        raise ConflictError("配对请求已被处理")
    row.status = DENIED
    row.denied_at = now
    row.denied_reason = (reason or "denied")[:255]
    row.code_hmac = ""
    audit_repo.add_audit(
        db,
        actor=actor,
        action="pairing.deny",
        target=request_id,
        detail={"reason": row.denied_reason},
    )
    db.commit()
    log_event(
        "info",
        "pairing.deny",
        "配对请求已被拒绝",
        request_id=request_id,
        actor=actor,
        reason=row.denied_reason,
    )
    return _as_item(row)


def list_pairings(
    db: Session,
    settings: Settings,
    *,
    status: str | None = None,
    limit: int = 100,
) -> list[dict]:
    now = utcnow()
    db.execute(
        update(PairingRequest)
        .where(PairingRequest.status.in_(ACTIVE_STATUSES), PairingRequest.expires_at <= now)
        .values(status=EXPIRED, denied_at=now, denied_reason="expired")
    )
    db.commit()

    statement = select(PairingRequest)
    if status:
        statement = statement.where(PairingRequest.status == status)
    statement = statement.order_by(PairingRequest.created_at.desc()).limit(max(1, min(limit, 500)))
    return [_as_item(row) for row in db.scalars(statement)]


def expire_pairings(db: Session, settings: Settings, now: datetime | None = None) -> tuple[int, int]:
    """worker 定时调用：过期活跃请求，并清理超过保留期的历史记录。"""
    now = now or utcnow()
    expired = db.execute(
        update(PairingRequest)
        .where(PairingRequest.status.in_(ACTIVE_STATUSES), PairingRequest.expires_at <= now)
        .values(status=EXPIRED, denied_at=now, denied_reason="expired")
    )
    cutoff = now - timedelta(days=settings.pairing_retention_days)
    removed = db.execute(
        delete(PairingRequest).where(
            PairingRequest.status.in_((DENIED, EXPIRED, CLAIMED)),
            PairingRequest.expires_at <= cutoff,
        )
    )
    db.commit()
    return getattr(expired, "rowcount", 0) or 0, getattr(removed, "rowcount", 0) or 0
