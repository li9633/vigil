"""管理端路由包。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.admin import notifications, reads

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
router.include_router(reads.router)
router.include_router(notifications.router)
