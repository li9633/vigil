"""管理端登录接口。"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import client_ip
from app.api.responses import ApiResponse, ok
from app.db import get_session
from app.schemas import LoginRequest, LoginResponse
from app.services.auth_service import login_admin
from app.settings import Settings, settings_from_app

router = APIRouter()


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
) -> ApiResponse[LoginResponse]:
    actor = login_admin(
        db,
        settings,
        username=payload.username,
        password=payload.password,
        client_key=client_ip(request),
    )
    request.session["admin"] = actor
    return ok(LoginResponse(actor=actor, server_time=int(time.time())))
