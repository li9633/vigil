"""ORM → 响应 DTO 的映射。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.models import AuditLog, Client, ClientEvent, Heartbeat, Incident, Notification
from app.schemas import iso_utc
from app.schemas.admin import (
    AuditItem,
    CheckStatusItem,
    ClientSummary,
    EventItem,
    GuardStatusItem,
    HeartbeatItem,
    IncidentItem,
)
from app.settings import Settings


def _load_json(text: str | None) -> Any:
    try:
        return json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}


def client_summary(
    client: Client,
    settings: Settings,
    now: datetime,
    last_incident: Incident | None = None,
) -> ClientSummary:
    online = (
        client.enabled
        and client.last_seen is not None
        and (now - client.last_seen).total_seconds() <= settings.suspect_seconds
    )
    revoked = not client.enabled or client.revoked_at is not None
    if revoked:
        lifecycle = "REVOKED"
        health = "UNKNOWN"
    else:
        lifecycle = "ONLINE" if online else "OFFLINE"
        if last_incident is not None and last_incident.recovered_at is None:
            health = last_incident.state
        elif online:
            health = "HEALTHY"
        else:
            health = "UNKNOWN"
    return ClientSummary(
        client_id=client.client_id,
        name=client.name,
        model=client.model,
        fw_version=client.fw_version,
        agent_version=client.agent_version,
        last_seen=iso_utc(client.last_seen),
        last_beacon=iso_utc(client.last_beacon),
        boot_id=client.boot_id,
        wan_ip=client.wan_ip,
        enabled=client.enabled,
        online=online,
        lifecycle=lifecycle,
        state=health,
        revoked_at=iso_utc(client.revoked_at),
        revoked_reason=client.revoked_reason,
        last_incident=incident_item(last_incident) if last_incident else None,
        created_at=iso_utc(client.created_at),
    )


def incident_item(incident: Incident) -> IncidentItem:
    evidence = _load_json(incident.evidence_json)
    return IncidentItem(
        id=incident.id,
        client_id=incident.client_id,
        guard_id=incident.guard_id,
        guard_name=incident.guard_name,
        category=incident.category,
        state=incident.state,
        started_at=iso_utc(incident.started_at),
        detected_at=iso_utc(incident.detected_at),
        recovered_at=iso_utc(incident.recovered_at),
        acked_at=iso_utc(incident.acked_at),
        notified_at=iso_utc(incident.notified_at),
        summary=incident.summary,
        evidence=evidence if isinstance(evidence, dict) else {},
    )


def audit_item(record: AuditLog) -> AuditItem:
    return AuditItem(
        id=record.id,
        actor=record.actor,
        action=record.action,
        target=record.target,
        ts=iso_utc(record.ts),
        detail=record.detail,
    )


def notification_item(record: Notification) -> dict[str, Any]:
    return {
        "id": record.id,
        "incident_id": record.incident_id,
        "client_id": record.client_id,
        "channel": record.channel,
        "target": record.target,
        "kind": record.kind,
        "status": record.status,
        "attempts": record.attempts,
        "last_error": record.last_error,
        "next_attempt_at": iso_utc(record.next_attempt_at),
        "created_at": iso_utc(record.created_at),
        "sent_at": iso_utc(record.sent_at),
    }


def heartbeat_item(heartbeat: Heartbeat) -> HeartbeatItem:
    checks = _load_json(heartbeat.checks_json)
    probes = _load_json(heartbeat.probes_json)
    guards = _load_json(heartbeat.guards_json)
    received_at = iso_utc(heartbeat.received_at)
    return HeartbeatItem(
        received_at=received_at,
        ts=received_at,
        seq=heartbeat.seq,
        uptime=heartbeat.uptime,
        boot_id=heartbeat.boot_id,
        wan_ip=heartbeat.wan_ip,
        checks=checks if isinstance(checks, dict) else {},
        probes=probes if isinstance(probes, list) else [],
        guards=guards if isinstance(guards, dict) else {},
    )


def event_item(event: ClientEvent) -> EventItem:
    payload = _load_json(event.payload_json)
    if not isinstance(payload, dict):
        payload = {"value": payload}
    return EventItem(
        event_id=event.event_id,
        client_id=event.client_id,
        ts=iso_utc(event.received_at),
        type=event.type,
        domain=event.domain,
        module=event.domain or None,
        level=payload.get("status") or payload.get("level"),
        message=payload.get("message") or payload.get("reason") or None,
        payload=payload,
    )


def guards_from_heartbeat(heartbeat: Heartbeat | None) -> list[GuardStatusItem]:
    """把最近一次心跳的 guards 字典展开成数组。"""
    if heartbeat is None:
        return []
    guards = _load_json(heartbeat.guards_json)
    if not isinstance(guards, dict):
        return []
    items: list[GuardStatusItem] = []
    for guard_id, value in guards.items():
        if not isinstance(value, dict):
            continue
        items.append(
            GuardStatusItem(
                id=str(guard_id),
                name=value.get("name"),
                template=value.get("template"),
                status=value.get("status"),
                check_status=value.get("check_status"),
                failure_count=value.get("failure_count"),
                checks=value.get("checks") or {},
            )
        )
    return items


def checks_from_heartbeat(heartbeat: Heartbeat | None) -> list[CheckStatusItem]:
    """把最近一次心跳的 checks 字典展开成数组。"""
    if heartbeat is None:
        return []
    checks = _load_json(heartbeat.checks_json)
    if not isinstance(checks, dict):
        return []
    items: list[CheckStatusItem] = []
    for check_id, value in checks.items():
        if not isinstance(value, dict):
            continue
        evidence = value.get("evidence")
        items.append(
            CheckStatusItem(
                check_id=str(value.get("check_id") or check_id),
                name=value.get("name"),
                probe=value.get("probe"),
                status=value.get("status"),
                latency_ms=value.get("latency_ms"),
                evidence=evidence if isinstance(evidence, dict) else {},
            )
        )
    return items
