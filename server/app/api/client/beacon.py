"""客户端保活 beacon。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_client
from app.api.responses import ApiResponse, ok
from app.db import get_session
from app.models import Client
from app.schemas import BeaconRequest, BeaconResponse
from app.services import client_service

router = APIRouter()


@router.post("/beacon")
def beacon(
    payload: BeaconRequest,
    db: Session = Depends(get_session),
    client: Client = Depends(require_client),
) -> ApiResponse[BeaconResponse]:
    return ok(client_service.record_beacon(db, client, payload))
