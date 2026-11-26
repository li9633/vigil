"""管理端告警操作接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.api.responses import ApiResponse, ok
from app.db import get_session
from app.schemas.admin import IncidentItem
from app.services import incident_service

router = APIRouter()


@router.post("/incidents/{incident_id}/ack")
def ack_incident(
    incident_id: int,
    db: Session = Depends(get_session),
    actor: str = Depends(require_admin),
) -> ApiResponse[IncidentItem]:
    return ok(incident_service.ack_incident(db, incident_id, actor=actor))
