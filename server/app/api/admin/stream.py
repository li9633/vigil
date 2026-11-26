"""管理端 SSE 推送接口。

前端在收到 update 事件后重新拉取 REST 数据，因此这里只推送版本信息。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.api.deps import require_admin
from app.logstore import log_event
from app.services import stream_service

router = APIRouter()

STREAM_INTERVAL_SECONDS = 3
STREAM_PING_SECONDS = 25
STREAM_RETRY_MS = 5000


def _sse_frame(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _admin_event_stream(request: Request, *, once: bool) -> AsyncIterator[str]:
    yield f"retry: {STREAM_RETRY_MS}\n\n"

    try:
        previous = stream_service.snapshot()
    except Exception as exc:  # 首次读取失败不中断连接，下一轮再试
        log_event("warn", "admin.stream.snapshot", "读取推送版本失败", error=str(exc))
        previous = {}
    yield _sse_frame("ready", {"version": previous, "server_time": int(time.time())})
    if once:
        return

    last_ping = time.monotonic()
    while True:
        await asyncio.sleep(STREAM_INTERVAL_SECONDS)
        if await request.is_disconnected():
            return
        try:
            current = stream_service.snapshot()
        except Exception as exc:  # 数据库短暂锁定时保持连接，避免前端反复重连
            log_event("warn", "admin.stream.snapshot", "读取推送版本失败", error=str(exc))
            current = previous
        if current != previous:
            changed = sorted(key for key in current if current.get(key) != previous.get(key))
            previous = current
            yield _sse_frame(
                "update",
                {
                    "changed": changed,
                    "version": current,
                    "server_time": int(time.time()),
                },
            )
            last_ping = time.monotonic()
        elif time.monotonic() - last_ping >= STREAM_PING_SECONDS:
            last_ping = time.monotonic()
            yield ": ping\n\n"


@router.get("/stream")
async def admin_stream(
    request: Request,
    once: bool = Query(default=False, description="仅输出首帧后关闭，用于调试与探活"),
    _actor: str = Depends(require_admin),
) -> StreamingResponse:
    """管理端 SSE：数据版本变化时推送 update 事件，前端据此重新拉取 REST 数据。"""
    return StreamingResponse(
        _admin_event_stream(request, once=once),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
