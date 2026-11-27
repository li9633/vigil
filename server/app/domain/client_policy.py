"""下发给客户端的策略文档与 ETag 计算（schema v3）。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.settings import Settings


def build_policy(settings: Settings) -> dict[str, Any]:
    """构造 schema v3 策略配置。

    Check / Guard 由客户端本地管理，服务端只下发运行时策略。
    """
    return {
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


def policy_etag(settings: Settings) -> str:
    body = json.dumps(build_policy(settings), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]
