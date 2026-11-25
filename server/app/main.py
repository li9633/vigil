"""FastAPI 应用入口。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app.api_admin import router as admin_router
from app.api_client import router as client_router
from app.db import init_db, init_engine
from app.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建应用；建表在 lifespan 启动阶段执行，避免导入期文件副作用。"""
    resolved = settings or get_settings()
    init_engine(resolved.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        init_db()
        yield

    app = FastAPI(
        title="Vigil Server",
        version="0.1.0",
        description="Vigil 服务监测与自愈系统服务端",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.add_middleware(
        SessionMiddleware,
        secret_key=resolved.session_secret,
        session_cookie="vigil_session",
        max_age=resolved.session_max_age,
        same_site="strict",
        https_only=resolved.session_https_only,
    )
    app.include_router(client_router)
    app.include_router(admin_router)

    @app.get("/healthz", tags=["meta"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
