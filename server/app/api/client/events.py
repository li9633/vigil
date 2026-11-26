"""客户端事件批量上报。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_client
from app.api.responses import ApiResponse, ok
from app.db import get_session
from app.models import Client
from app.schemas import EventsRequest, EventsResponse
from app.services import client_service

router = APIRouter()


@router.post("/events")
def submit_events(
    payload: EventsRequest,
    db: Session = Depends(get_session),
    client: Client = Depends(require_client),
) -> ApiResponse[EventsResponse]:
    return ok(client_service.submit_events(db, client, payload))
