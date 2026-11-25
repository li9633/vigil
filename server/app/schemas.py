"""请求/响应模型（Pydantic v2）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


def iso_utc(value: datetime | None) -> str | None:
    """naive UTC 时间序列化为带 Z 后缀的 ISO 字符串。"""
    return None if value is None else value.isoformat() + "Z"


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    model: str = ""
    fw_version: str = ""
    agent_version: str = ""
    protocol: int = 1


class RegisterResponse(BaseModel):
    client_id: str
    secret: str
    server_time: int
    heartbeat_interval: int


class BeaconRequest(BaseModel):
    client_id: str
    seq: int = 0
    boot_id: str = ""
    uptime: int = 0


class SpoolStats(BaseModel):
    pending: int = 0
    dropped: int = 0


class HeartbeatRequest(BaseModel):
    client_id: str
    seq: int = 0
    boot_id: str = ""
    uptime: int = 0
    agent_version: str = ""
    wan_ip: str | None = None
    modules: dict[str, dict[str, Any]] = Field(default_factory=dict)
    guards: dict[str, dict[str, Any]] = Field(default_factory=dict)
    spool: SpoolStats | None = None


class BeaconResponse(BaseModel):
    ok: bool = True
    server_time: int


class HeartbeatResponse(BaseModel):
    ok: bool = True
    server_time: int
    config_etag: str
    actions_enabled: bool


class EventItem(BaseModel):
    event_id: str = Field(min_length=1, max_length=64)
    seq: int = 0
    type: str = ""
    domain: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class EventsRequest(BaseModel):
    client_id: str
    events: list[EventItem] = Field(default_factory=list)


class EventsResponse(BaseModel):
    acked: list[str]


class LoginRequest(BaseModel):
    username: str
    password: str


class NotificationTestRequest(BaseModel):
    channel: str = Field(default="all", pattern="^(feishu|wecom|email|all)$")
