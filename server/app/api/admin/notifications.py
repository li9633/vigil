"""管理端通知配置与发送记录。"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db import get_session, utcnow
from app.models import AuditLog
from app.schemas import NotificationTestRequest
from app.services import notification_service, serializers

router = APIRouter()


@router.get("/notifications")
def get_notifications(
    db: Session = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    config = notification_service.mask_config(notification_service.load_config(db))
    records = notification_service.list_recent_records(db, limit=20)
    return {
        "config": config,
        "records": [serializers.notification_item(record) for record in records],
    }


@router.put("/notifications")
def update_notifications(
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_session),
    actor: str = Depends(require_admin),
) -> dict[str, Any]:
    config = notification_service.save_config(db, payload)

    db.add(
        AuditLog(
            actor=actor,
            action="update_notifications",
            target="notifications",
            ts=utcnow(),
            detail=json.dumps(
                {
                    "enabled": [
                        channel
                        for channel in ("feishu", "wecom", "email")
                        if config[channel]["enabled"]
                    ],
                    "rate_limit_per_hour": config["rate_limit_per_hour"],
                },
                ensure_ascii=False,
            ),
        )
    )
    db.commit()
    return {"config": notification_service.mask_config(config)}


@router.post("/notifications/test")
def test_notifications(
    payload: NotificationTestRequest,
    db: Session = Depends(get_session),
    actor: str = Depends(require_admin),
) -> dict[str, Any]:
    results = notification_service.send_test_notifications(db, payload.channel)

    db.add(
        AuditLog(
            actor=actor,
            action="test_notification",
            target=payload.channel,
            ts=utcnow(),
            detail=json.dumps(
                [{"channel": item["channel"], "ok": item["ok"]} for item in results],
                ensure_ascii=False,
            ),
        )
    )
    db.commit()
    return {"results": results}


@router.get("/notifications/records")
def list_notification_records(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    records = notification_service.list_recent_records(db, limit=limit)
    return {"records": [serializers.notification_item(record) for record in records]}
