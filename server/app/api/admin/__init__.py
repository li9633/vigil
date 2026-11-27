"""管理端路由包。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.admin import (
    audit,
    auth,
    checks,
    incidents,
    logs,
    notifications,
    pairing,
    policies,
    reads,
    stream,
    system,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
router.include_router(auth.router)
router.include_router(reads.router)
router.include_router(incidents.router)
router.include_router(checks.router)
router.include_router(policies.router)
router.include_router(pairing.router)
router.include_router(system.router)
router.include_router(notifications.router)
router.include_router(audit.router)
router.include_router(logs.router)
router.include_router(stream.router)
