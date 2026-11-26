"""管理端运行时策略配置接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.api.responses import ApiResponse, ok
from app.db import get_session
from app.schemas.admin import PolicyResponse
from app.services import policy_service
from app.settings import Settings, settings_from_app

router = APIRouter()


@router.get("/policies")
def get_policies(
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
    _actor: str = Depends(require_admin),
) -> ApiResponse[PolicyResponse]:
    return ok(policy_service.get_policies(db, settings))


@router.put("/policies")
def update_policies(
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
    actor: str = Depends(require_admin),
) -> ApiResponse[PolicyResponse]:
    return ok(policy_service.update_policies(db, settings, payload, actor=actor))
