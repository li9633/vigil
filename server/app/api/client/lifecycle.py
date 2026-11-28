"""客户端生命周期接口：主动取消注册。"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_client
from app.api.responses import ApiResponse, ok
from app.db import get_session
from app.models import Client
from app.schemas import UnregisterRequest, UnregisterResponse
from app.services import client_service
from app.settings import Settings, settings_from_app

router = APIRouter()


@router.post("/unregister")
def unregister(
    payload: UnregisterRequest | None = Body(default=None),
    client: Client = Depends(require_client),
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
) -> ApiResponse[UnregisterResponse]:
    reason = payload.reason if payload else ""
    return ok(client_service.unregister_client(db, settings, client, reason=reason))
