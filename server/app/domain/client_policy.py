"""下发给客户端的策略文档与 ETag 计算（schema v3）。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.settings import Settings


def build_policy(
    settings: Settings,
    checks: list[dict[str, Any]] | None = None,
    guards: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """构造 schema v3 策略配置。

    服务端存在任一 checks / guards 时视为「服务端管理配置」，两者成对下发
    （可能为空数组），客户端整体替换本地配置；都为空时不下发这两个键，
    客户端回退本地模板。
    """
    policy: dict[str, Any] = {
        "schema_version": 3,
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
    if checks or guards:
        policy["checks"] = checks or []
        policy["guards"] = guards or []
    return policy


def policy_etag(
    settings: Settings,
    checks: list[dict[str, Any]] | None = None,
    guards: list[dict[str, Any]] | None = None,
) -> str:
    body = json.dumps(
        build_policy(settings, checks, guards), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]
