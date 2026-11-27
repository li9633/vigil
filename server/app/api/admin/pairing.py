"""管理端配对审批：查看待审批请求、输入配对码批准或拒绝。"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.api.responses import ApiResponse, ok
from app.db import get_session
from app.schemas.admin import (
    PairingApproveRequest,
    PairingDenyRequest,
    PairingItem,
    PairingListResponse,
)
from app.services import pairing_service
from app.settings import Settings, settings_from_app

router = APIRouter()


@router.get("/pairing")
def list_pairings(
    status: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
    _actor: str = Depends(require_admin),
) -> ApiResponse[PairingListResponse]:
    items = pairing_service.list_pairings(db, settings, status=status, limit=limit)
    return ok(PairingListResponse(items=[PairingItem(**item) for item in items]))


@router.post("/pairing/{request_id}/approve")
def approve_pairing(
    request_id: str,
    payload: PairingApproveRequest,
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
    actor: str = Depends(require_admin),
) -> ApiResponse[PairingItem]:
    item = pairing_service.approve_pairing(
        db,
        settings,
        request_id=request_id,
        code=payload.code,
        actor=actor,
    )
    return ok(PairingItem(**item))


@router.post("/pairing/{request_id}/deny")
def deny_pairing(
    request_id: str,
    payload: PairingDenyRequest | None = Body(default=None),
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
    actor: str = Depends(require_admin),
) -> ApiResponse[PairingItem]:
    reason = payload.reason if payload else ""
    item = pairing_service.deny_pairing(
        db,
        settings,
        request_id=request_id,
        reason=reason,
        actor=actor,
    )
    return ok(PairingItem(**item))
