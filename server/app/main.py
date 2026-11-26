"""FastAPI 应用入口。"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware

from app.api.admin import router as admin_router
from app.api.error_handlers import register_exception_handlers
from app.api_client import router as client_router
from app.db import init_db, init_engine
from app.logstore import configure_logging, log_event, reset_request_id, set_request_id
from app.settings import Settings, get_settings

ROUTINE_CLIENT_PATHS = {
    "/api/v1/heartbeat",
    "/api/v1/beacon",
    "/api/v1/events",
    "/healthz",
}


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建应用；建表在 lifespan 启动阶段执行，避免导入期文件副作用。"""
    resolved = settings or get_settings()
    init_engine(resolved.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        configure_logging("api", resolved)
        init_db()
        log_event("info", "app.start", "Vigil API 启动", version="0.1.0")
        yield
        log_event("info", "app.stop", "Vigil API 停止")

    logging.getLogger("uvicorn.access").disabled = True

    app = FastAPI(
        title="Vigil Server",
        version="0.1.0",
        description="Vigil 服务监测与自愈系统服务端",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    register_exception_handlers(app)
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

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        request_id = uuid.uuid4().hex[:16]
        token = set_request_id(request_id)
        started = time.monotonic()
        try:
            response = await call_next(request)
            duration_ms = int((time.monotonic() - started) * 1000)
            if response.status_code >= 400 or (
                request.method != "GET"
                and request.url.path not in ROUTINE_CLIENT_PATHS
            ):
                log_event(
                    "warn" if response.status_code >= 400 else "info",
                    "api.request",
                    "API 请求",
                    method=request.method,
                    path=request.url.path,
                    status=response.status_code,
                    duration_ms=duration_ms,
                )
            response.headers["X-Request-Id"] = request_id
            return response
        except Exception:
            log_event(
                "error",
                "server.error",
                "请求处理异常",
                method=request.method,
                path=request.url.path,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            raise
        finally:
            reset_request_id(token)

    @app.get("/healthz", tags=["meta"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
