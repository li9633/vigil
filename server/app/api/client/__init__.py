"""客户端接入路由：注册、配对、生命周期、beacon、heartbeat、events、config。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.client import (
    beacon,
    config,
    events,
    heartbeat,
    lifecycle,
    pairing,
    register,
)

router = APIRouter(prefix="/api/v1", tags=["client"])
router.include_router(register.router)
router.include_router(pairing.router)
router.include_router(lifecycle.router)
router.include_router(beacon.router)
router.include_router(heartbeat.router)
router.include_router(events.router)
router.include_router(config.router)
