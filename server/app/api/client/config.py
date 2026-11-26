"""策略配置下发：支持 ETag 协商与 304。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import require_client
from app.api.responses import ApiResponse, ok
from app.db import get_session
from app.models import Client
from app.services import client_service
from app.settings import Settings, settings_from_app

router = APIRouter()


@router.get("/config", response_model=ApiResponse[dict[str, Any]])
def get_config(
    request: Request,
    response: Response,
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
    client: Client = Depends(require_client),
) -> Any:
    policy, etag = client_service.load_policy(db, client, settings)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return ok(policy)
