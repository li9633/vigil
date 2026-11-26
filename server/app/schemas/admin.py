"""管理端只读接口的响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class IncidentItem(BaseModel):
    id: int
    client_id: str
    guard_id: str | None
    guard_name: str | None
    category: str
    state: str
    started_at: str | None
    detected_at: str | None
    recovered_at: str | None
    acked_at: str | None
    notified_at: str | None
    summary: str
    evidence: dict[str, Any]


class ClientSummary(BaseModel):
    client_id: str
    name: str
    model: str
    fw_version: str
    agent_version: str
    last_seen: str | None
    last_beacon: str | None
    boot_id: str | None
    wan_ip: str | None
    enabled: bool
    online: bool
    state: str
    last_incident: IncidentItem | None = None
    created_at: str | None


class HeartbeatItem(BaseModel):
    received_at: str | None
    ts: str | None
    seq: int
    uptime: int
    boot_id: str
    wan_ip: str | None
    modules: dict[str, Any]
    guards: dict[str, Any]


class EventItem(BaseModel):
    event_id: str
    client_id: str
    ts: str | None
    type: str
    domain: str
    module: str | None
    level: str | None
    message: str | None
    payload: dict[str, Any]


class GuardStatusItem(BaseModel):
    id: str
    name: str | None = None
    template: str | None = None
    status: str | None = None
    check_status: str | None = None
    failure_count: int | None = None
    checks: dict[str, Any]


class ModuleStatusItem(BaseModel):
    id: str
    status: str | None = None
    latency_ms: int | None = None
    uptime: int | None = None


class TimelineItem(BaseModel):
    id: int
    ts: str | None
    type: str
    severity: str
    client_id: str
    summary: str


class OverviewResponse(BaseModel):
    clients_total: int
    clients_online: int
    clients_down: int
    incidents_open: int
    open_incidents: int
    incidents_24h: int
    flapping_clients: int
    notification_success_rate: float | None = None
    timeline: list[TimelineItem]
    generated_at: str | None
    server_time: int


class ClientListResponse(BaseModel):
    items: list[ClientSummary]


class ClientDetailResponse(BaseModel):
    client: ClientSummary
    modules: list[ModuleStatusItem]
    guards: list[GuardStatusItem]
    heartbeats: list[HeartbeatItem]
    incidents: list[IncidentItem]
    recent_events: list[EventItem]
    actions: list[Any]
    generated_at: str | None


class IncidentListResponse(BaseModel):
    items: list[IncidentItem]
