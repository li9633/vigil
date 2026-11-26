"""管理端检测项（Check）配置与 probe 目录接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.api.responses import ApiResponse, ok
from app.db import get_session
from app.schemas.admin import CheckListResponse, ProbeCatalogResponse
from app.services import check_service

router = APIRouter()


@router.get("/clients/{client_id}/checks")
def get_client_checks(
    client_id: str,
    db: Session = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> ApiResponse[CheckListResponse]:
    return ok(check_service.list_checks(db, client_id))


@router.put("/clients/{client_id}/checks")
def update_client_checks(
    client_id: str,
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_session),
    actor: str = Depends(require_admin),
) -> ApiResponse[CheckListResponse]:
    return ok(check_service.replace_checks(db, client_id, payload, actor=actor))


@router.get("/clients/{client_id}/probes")
def get_client_probes(
    client_id: str,
    db: Session = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> ApiResponse[ProbeCatalogResponse]:
    return ok(check_service.get_probe_catalog(db, client_id))
