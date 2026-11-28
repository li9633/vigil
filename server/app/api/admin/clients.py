"""管理端客户端写接口：彻底删除客户端。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.api.responses import ApiResponse, ok
from app.db import get_session
from app.schemas.admin import DeleteClientResponse
from app.services import client_admin_service

router = APIRouter()


@router.delete("/clients/{client_id}")
def delete_client(
    client_id: str,
    db: Session = Depends(get_session),
    actor: str = Depends(require_admin),
) -> ApiResponse[DeleteClientResponse]:
    data = client_admin_service.delete_client(db, client_id, actor=actor)
    return ok(DeleteClientResponse(**data))
