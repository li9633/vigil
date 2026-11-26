"""HTTP 层公共依赖：管理员鉴权、客户端鉴权与请求来源解析。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db import get_session, utcnow
from app.models import Client
from app.security import authenticate_client
from app.settings import Settings


def require_admin(request: Request) -> str:
    actor = request.session.get("admin")
    if not actor:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录",
        )
    return str(actor)


def require_client(
    request: Request,
    x_vigil_client: Annotated[str | None, Header(alias="X-Vigil-Client")] = None,
    authorization: Annotated[str | None, Header()] = None,
    x_vigil_timestamp: Annotated[str | None, Header(alias="X-Vigil-Timestamp")] = None,
    x_vigil_nonce: Annotated[str | None, Header(alias="X-Vigil-Nonce")] = None,
    db: Session = Depends(get_session),
) -> Client:
    settings: Settings = request.app.state.settings
    return authenticate_client(
        db,
        settings,
        client_id=x_vigil_client,
        authorization=authorization,
        timestamp=x_vigil_timestamp,
        nonce=x_vigil_nonce,
        now=utcnow(),
    )


def client_ip(request: Request) -> str:
    """取真实来源 IP；服务位于 nginx 之后，优先使用代理写入的头部。"""
    forwarded = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
