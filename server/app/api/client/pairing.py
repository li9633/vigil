"""客户端配对：发起请求、凭 claim_token 轮询并领取凭证。

该路由不依赖 bootstrap token，由管理员在服务端确认配对码后放行。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from app.api.deps import client_ip
from app.api.responses import ApiResponse, ok
from app.db import get_session
from app.schemas import PairingStartRequest, PairingStartResponse, PairingStatusResponse
from app.security import parse_pair_authorization
from app.services import pairing_service
from app.settings import Settings, settings_from_app

router = APIRouter()


@router.post("/pairing/requests")
def start_pairing(
    payload: PairingStartRequest,
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
) -> ApiResponse[PairingStartResponse]:
    data = pairing_service.start_pairing(
        db,
        settings,
        payload,
        client_ip=client_ip(request),
    )
    return ok(PairingStartResponse(**data))


@router.get("/pairing/requests/{request_id}")
def pairing_status(
    request_id: str,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
) -> ApiResponse[PairingStatusResponse]:
    claim_token = parse_pair_authorization(authorization)
    data = pairing_service.pairing_status(
        db,
        settings,
        request_id=request_id,
        claim_token=claim_token,
    )
    return ok(PairingStatusResponse(**data))
