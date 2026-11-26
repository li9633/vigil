"""客户端心跳。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_client
from app.api.responses import ApiResponse, ok
from app.db import get_session
from app.models import Client
from app.schemas import HeartbeatRequest, HeartbeatResponse
from app.services import client_service
from app.settings import Settings, settings_from_app

router = APIRouter()


@router.post("/heartbeat")
def heartbeat(
    payload: HeartbeatRequest,
    db: Session = Depends(get_session),
    client: Client = Depends(require_client),
    settings: Settings = Depends(settings_from_app),
) -> ApiResponse[HeartbeatResponse]:
    return ok(client_service.record_heartbeat(db, client, payload, settings))
