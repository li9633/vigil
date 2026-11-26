"""管理端守护项（Guard）配置接口：整体读写，供 /api/v1/config 下发。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.api.responses import ApiResponse, ok
from app.db import get_session
from app.schemas.admin import GuardListResponse
from app.services import guard_service

router = APIRouter()


@router.get("/clients/{client_id}/guards")
def get_client_guards(
    client_id: str,
    db: Session = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> ApiResponse[GuardListResponse]:
    return ok(guard_service.list_guards(db, client_id))


@router.put("/clients/{client_id}/guards")
def update_client_guards(
    client_id: str,
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_session),
    actor: str = Depends(require_admin),
) -> ApiResponse[GuardListResponse]:
    return ok(guard_service.replace_guards(db, client_id, payload, actor=actor))


@router.delete("/clients/{client_id}/guards")
def clear_client_guards(
    client_id: str,
    db: Session = Depends(get_session),
    actor: str = Depends(require_admin),
) -> ApiResponse[GuardListResponse]:
    return ok(guard_service.clear_guards(db, client_id, actor=actor))
