"""客户端注册。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.api.responses import ApiResponse, ok
from app.db import get_session
from app.schemas import RegisterRequest, RegisterResponse
from app.services import client_service
from app.settings import Settings, settings_from_app

router = APIRouter()


@router.post("/register")
def register(
    payload: RegisterRequest,
    x_vigil_bootstrap: Annotated[str | None, Header(alias="X-Vigil-Bootstrap")] = None,
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
) -> ApiResponse[RegisterResponse]:
    return ok(
        client_service.register_client(
            db,
            settings,
            bootstrap_token=x_vigil_bootstrap,
            payload=payload,
        )
    )
