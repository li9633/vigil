"""HTTP 层公共依赖：管理员鉴权与请求来源解析。"""

from __future__ import annotations

from fastapi import HTTPException, Request, status


def require_admin(request: Request) -> str:
    actor = request.session.get("admin")
    if not actor:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录",
        )
    return str(actor)


def client_ip(request: Request) -> str:
    """取真实来源 IP；服务位于 nginx 之后，优先使用代理写入的头部。"""
    forwarded = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
