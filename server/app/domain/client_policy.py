"""下发给客户端的策略文档与 ETag 计算。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.settings import Settings


def build_policy(
    settings: Settings, guards: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """构造策略配置；仅在存在服务端 Guard 时附带 guards。"""
    policy: dict[str, Any] = {
        "schema_version": 1,
        "heartbeat_interval": settings.heartbeat_interval,
        "beacon_interval": 60,
        "thresholds": {
            "suspect_seconds": settings.suspect_seconds,
            "down_seconds": settings.down_seconds,
            "beacon_seconds": settings.beacon_seconds,
        },
        "actions_enabled": settings.actions_enabled,
        "action": {
            "max_level": "L1",
            "cooldown_seconds": settings.action_cooldown_seconds,
            "max_per_hour": settings.action_max_per_hour,
        },
        "config_poll_interval": 300,
    }
    if guards:
        policy["guards"] = guards
    return policy


def policy_etag(settings: Settings, guards: list[dict[str, Any]] | None = None) -> str:
    body = json.dumps(build_policy(settings, guards), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]
