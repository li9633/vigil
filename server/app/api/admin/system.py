"""管理端系统设置接口：系统信息、管理员密码与客户端密钥轮换。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.api.responses import ApiResponse, ok
from app.db import get_session
from app.schemas.admin import (
    AdminPasswordUpdateResponse,
    RotateSecretResponse,
    SystemSettingsResponse,
)
from app.services import system_service
from app.settings import Settings, settings_from_app

router = APIRouter()


@router.get("/system")
def get_system_settings(
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
    _actor: str = Depends(require_admin),
) -> ApiResponse[SystemSettingsResponse]:
    return ok(system_service.get_system_settings(db, settings))


@router.put("/system/admin-password")
def update_admin_password(
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
    actor: str = Depends(require_admin),
) -> ApiResponse[AdminPasswordUpdateResponse]:
    return ok(
        system_service.change_admin_password(
            db,
            settings,
            actor=actor,
            current_password=str(payload.get("current_password") or ""),
            new_password=str(payload.get("new_password") or ""),
        )
    )


@router.post("/clients/{client_id}/secret/rotate")
def rotate_client_secret(
    client_id: str,
    db: Session = Depends(get_session),
    actor: str = Depends(require_admin),
) -> ApiResponse[RotateSecretResponse]:
    """轮换客户端 secret，明文仅在本次响应中返回一次。"""
    return ok(system_service.rotate_client_secret(db, client_id, actor=actor))
