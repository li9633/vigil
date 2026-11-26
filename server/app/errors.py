"""领域异常：由 service/repository 层抛出，HTTP 状态映射在 api 层完成。"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """所有可预期业务异常的基类。"""

    code = "error"

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class ValidationError(AppError):
    code = "validation_failed"


class UnauthorizedError(AppError):
    code = "unauthorized"


class ForbiddenError(AppError):
    code = "forbidden"


class NotFoundError(AppError):
    code = "not_found"


class ConflictError(AppError):
    code = "conflict"


class RateLimitedError(AppError):
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after: int, details: Any = None) -> None:
        super().__init__(message, details=details)
        self.retry_after = retry_after


class InternalError(AppError):
    code = "internal_error"
