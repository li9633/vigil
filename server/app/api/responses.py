"""统一响应结构。

约定：
- HTTP 状态码保持语义（401 仍用于跳登录、429 仍携带 Retry-After）；
- 成功体：`{"code": "ok", "message": "ok", "data": ...}`；
- 失败体由 `app.api.error_handlers` 统一生成，结构与成功体一致；
- 健康检查、文件导出、SSE 等基础设施端点保持裸响应，不套本结构。
"""

from __future__ import annotations

from pydantic import BaseModel


class ApiResponse[T](BaseModel):
    code: str = "ok"
    message: str = "ok"
    data: T | None = None


def ok[T](data: T | None = None, message: str = "ok") -> ApiResponse[T]:
    """构造成功响应；路由直接在返回值中使用。"""
    return ApiResponse[T](data=data, message=message)
