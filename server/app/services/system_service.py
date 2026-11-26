"""系统设置用例：系统信息、管理员密码变更与客户端密钥轮换。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db import utcnow
from app.errors import NotFoundError, ValidationError
from app.logstore import log_event
from app.policies import effective_settings
from app.repositories import audit_repo, client_repo
from app.schemas import iso_utc
from app.schemas.admin import (
    AdminPasswordUpdateResponse,
    RotateSecretResponse,
    SystemSettingsResponse,
)
from app.security import hash_secret, new_client_secret
from app.settings import Settings
from app.system_settings import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    admin_auth_state,
    load_pepper,
    remove_initial_password,
    set_admin_password,
    verify_admin_password,
)


def get_system_settings(db: Session, settings: Settings) -> SystemSettingsResponse:
    effective = effective_settings(db, settings)
    state = admin_auth_state(db, settings.admin_key_file)
    return SystemSettingsResponse(
        admin_username=settings.admin_username,
        password_updated_at=state["updated_at"],
        retention_days=effective.retention_days,
        disk_alert_percent=effective.disk_alert_percent,
        disk_alert_cooldown_seconds=effective.disk_alert_cooldown_seconds,
    )


def change_admin_password(
    db: Session,
    settings: Settings,
    *,
    actor: str,
    current_password: str,
    new_password: str,
) -> AdminPasswordUpdateResponse:
    if not verify_admin_password(db, current_password, settings.admin_key_file):
        raise ValidationError("当前密码不正确")
    if not MIN_PASSWORD_LENGTH <= len(new_password) <= MAX_PASSWORD_LENGTH:
        raise ValidationError(
            f"新密码长度需在 {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} 位之间"
        )
    if current_password == new_password:
        raise ValidationError("新密码不能与当前密码相同")

    record = set_admin_password(
        db, new_password, load_pepper(settings.admin_key_file, create=True)
    )
    audit_repo.add_audit(
        db,
        actor=actor,
        action="update_admin_password",
        target="admin",
        detail={},
    )
    remove_initial_password(settings.admin_key_file)
    db.commit()
    log_event("warn", "auth.admin.password", "管理员密码已更新", actor=actor)
    updated_at = record.get("updated_at")
    return AdminPasswordUpdateResponse(
        password_updated_at=str(updated_at) if updated_at else None,
    )


def rotate_client_secret(
    db: Session, client_id: str, *, actor: str
) -> RotateSecretResponse:
    """轮换客户端 secret，明文仅在本次响应中返回一次。"""
    client = client_repo.get_client(db, client_id)
    if client is None:
        raise NotFoundError("客户端不存在")

    secret = new_client_secret()
    client.secret_sha256 = hash_secret(secret)
    rotated_at = utcnow()
    audit_repo.add_audit(
        db,
        actor=actor,
        action="rotate_client_secret",
        target=client_id,
        ts=rotated_at,
        detail={"client_id": client_id},
    )
    db.commit()
    log_event(
        "warn", "auth.client.rotate", "客户端 secret 已轮换", client_id=client_id, actor=actor
    )
    return RotateSecretResponse(
        client_id=client_id,
        secret=secret,
        rotated_at=iso_utc(rotated_at),
    )
