"""管理端只读接口：总览、客户端、告警。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.api.responses import ApiResponse, ok
from app.db import get_session
from app.schemas.admin import (
    ClientDetailResponse,
    ClientListResponse,
    IncidentListResponse,
    OverviewResponse,
)
from app.services import admin_read_service
from app.settings import Settings, settings_from_app

router = APIRouter()


@router.get("/overview")
def overview(
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
    _actor: str = Depends(require_admin),
) -> ApiResponse[OverviewResponse]:
    return ok(admin_read_service.get_overview(db, settings))


@router.get("/clients")
def list_clients(
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
    _actor: str = Depends(require_admin),
) -> ApiResponse[ClientListResponse]:
    return ok(admin_read_service.list_clients(db, settings))


@router.get("/clients/{client_id}")
def get_client_detail(
    client_id: str,
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
    _actor: str = Depends(require_admin),
) -> ApiResponse[ClientDetailResponse]:
    return ok(admin_read_service.get_client_detail(db, settings, client_id))


@router.get("/incidents")
def list_incidents(
    client_id: str | None = Query(default=None),
    state: str | None = Query(default=None),
    category: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> ApiResponse[IncidentListResponse]:
    return ok(
        admin_read_service.list_incidents(
            db, client_id=client_id, state=state, category=category, limit=limit
        )
    )
