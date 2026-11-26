"""管理端 SSE 推送的版本快照。

API 与 worker 是两个进程，状态变更发生在 worker，因此由客户端轮询数据库
聚合版本：只有版本变化时才通知前端重新拉取 REST 数据。
"""

from __future__ import annotations

from typing import Any

from app.db import session_scope
from app.repositories import stream_repo
from app.schemas import iso_utc


def snapshot() -> dict[str, list[Any]]:
    """读取各业务表的最小版本信息（聚合值，不含具体记录）。"""
    with session_scope() as db:
        versions = stream_repo.snapshot_versions(db)

    incidents = versions["incidents"]
    clients = versions["clients"]
    guards = versions["guards"]
    notifications = versions["notifications"]
    return {
        "incidents": [
            incidents[0],
            incidents[1],
            iso_utc(incidents[2]),
            iso_utc(incidents[3]),
            iso_utc(incidents[4]),
        ],
        "clients": [clients[0], iso_utc(clients[1])],
        "guards": [guards[0], iso_utc(guards[1])],
        "notifications": [notifications[0], notifications[1], iso_utc(notifications[2])],
    }
