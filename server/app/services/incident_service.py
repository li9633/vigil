"""告警确认用例。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db import utcnow
from app.errors import NotFoundError
from app.repositories import audit_repo, incident_repo
from app.schemas.admin import IncidentItem
from app.services import serializers


def ack_incident(db: Session, incident_id: int, *, actor: str) -> IncidentItem:
    incident = incident_repo.get(db, incident_id)
    if incident is None:
        raise NotFoundError("incident 不存在")

    now = utcnow()
    if incident.acked_at is None:
        incident.acked_at = now
    audit_repo.add_audit(
        db,
        actor=actor,
        action="ack_incident",
        target=str(incident.id),
        ts=now,
        detail={"client_id": incident.client_id, "category": incident.category},
    )
    db.commit()
    return serializers.incident_item(incident)
