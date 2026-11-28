"""管理端只读接口的响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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
    # 连接生命周期：ONLINE / OFFLINE / REVOKED
    lifecycle: str
    # 健康状态：HEALTHY / SUSPECT / DOWN / UNKNOWN（与连接状态分开）
    state: str
    revoked_at: str | None = None
    revoked_reason: str | None = None
    last_incident: IncidentItem | None = None
    created_at: str | None


class HeartbeatItem(BaseModel):
    received_at: str | None
    ts: str | None
    seq: int
    uptime: int
    boot_id: str
    wan_ip: str | None
    checks: dict[str, Any]
    probes: list[dict[str, Any]]
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


class CheckStatusItem(BaseModel):
    check_id: str
    name: str | None = None
    probe: str | None = None
    status: str | None = None
    latency_ms: int | None = None
    evidence: dict[str, Any]


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
    checks: list[CheckStatusItem]
    guards: list[GuardStatusItem]
    heartbeats: list[HeartbeatItem]
    incidents: list[IncidentItem]
    recent_events: list[EventItem]
    actions: list[Any]
    generated_at: str | None


class IncidentListResponse(BaseModel):
    items: list[IncidentItem]


class ProbeCatalogResponse(BaseModel):
    items: list[dict[str, Any]]
    updated_at: str | None = None


class PolicyResponse(BaseModel):
    policies: dict[str, Any]
    defaults: dict[str, Any]


class SystemSettingsResponse(BaseModel):
    admin_username: str
    password_updated_at: str | None = None
    retention_days: int
    disk_alert_percent: int
    disk_alert_cooldown_seconds: int


class AdminPasswordUpdateResponse(BaseModel):
    ok: bool = True
    password_updated_at: str | None = None


class RotateSecretResponse(BaseModel):
    client_id: str
    secret: str
    rotated_at: str | None


class AuditItem(BaseModel):
    id: int
    actor: str
    action: str
    target: str
    ts: str | None
    detail: str | None


class AuditListResponse(BaseModel):
    items: list[AuditItem]
    total: int


class PairingItem(BaseModel):
    request_id: str
    instance_id: str
    name: str
    model: str
    fw_version: str
    agent_version: str
    client_ip: str
    status: str
    attempts: int
    client_id: str
    created_at: str | None
    expires_at: str | None
    code_expires_at: str | None
    approved_at: str | None
    approved_by: str | None
    claimed_at: str | None
    denied_at: str | None
    denied_reason: str | None


class PairingListResponse(BaseModel):
    items: list[PairingItem]


class PairingApproveRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)


class PairingDenyRequest(BaseModel):
    reason: str = Field(default="", max_length=255)


class DeleteClientResponse(BaseModel):
    client_id: str
    deleted: bool
