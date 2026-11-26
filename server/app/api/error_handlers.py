"""统一错误响应：把各类异常收敛成 `{code, message, data}`。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.errors import (
    AppError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    RateLimitedError,
    UnauthorizedError,
    ValidationError,
)

# 顺序敏感：子类必须先于 AppError 基类匹配
_STATUS_BY_ERROR: tuple[tuple[type[AppError], int], ...] = (
    (RateLimitedError, 429),
    (ValidationError, 400),
    (UnauthorizedError, 401),
    (ForbiddenError, 403),
    (NotFoundError, 404),
    (ConflictError, 409),
)

_CODE_BY_STATUS = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_failed",
    429: "rate_limited",
    500: "internal_error",
}


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"code": code, "message": message, "data": None, "details": details}


def _status_for(exc: AppError) -> int:
    for error_type, status_code in _STATUS_BY_ERROR:
        if isinstance(exc, error_type):
            return status_code
    return 500


def _validation_details(exc: RequestValidationError) -> list[dict[str, Any]]:
    return [
        {
            "field": ".".join(str(part) for part in error.get("loc", ())),
            "message": error.get("msg", ""),
        }
        for error in exc.errors()
    ]


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        headers = None
        if isinstance(exc, RateLimitedError):
            headers = {"Retry-After": str(exc.retry_after)}
        return JSONResponse(
            status_code=_status_for(exc),
            content=error_body(exc.code, exc.message, exc.details),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body(
                "validation_failed", "请求参数校验失败", _validation_details(exc)
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_error(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        detail = exc.detail
        message = detail if isinstance(detail, str) else "请求失败"
        code = _CODE_BY_STATUS.get(exc.status_code, "http_error")
        details = None if isinstance(detail, str) else detail
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, message, details),
            headers=exc.headers,
        )
