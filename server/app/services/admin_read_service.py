"""管理端只读用例：总览、客户端列表/详情、告警列表。"""

from __future__ import annotations

import time
from datetime import timedelta

from sqlalchemy.orm import Session

from app.db import utcnow
from app.errors import NotFoundError
from app.policies import effective_settings
from app.repositories import (
    client_repo,
    event_repo,
    heartbeat_repo,
    incident_repo,
    overview_repo,
)
from app.schemas import iso_utc
from app.schemas.admin import (
    ClientDetailResponse,
    ClientListResponse,
    IncidentListResponse,
    OverviewResponse,
    TimelineItem,
)
from app.services import serializers
from app.settings import Settings
from app.state_machine import CATEGORY_AGENT_DOWN, CATEGORY_HOST_OR_NET_DOWN


def get_overview(db: Session, settings: Settings) -> OverviewResponse:
    now = utcnow()
    effective = effective_settings(db, settings)

    clients_total = overview_repo.count_clients(db)
    clients_online = overview_repo.count_online_clients(
        db, now - timedelta(seconds=effective.suspect_seconds)
    )
    incidents_open = overview_repo.count_open_incidents(db)
    incidents_24h = overview_repo.count_incidents_since(db, now - timedelta(hours=24))
    timeline = [
        TimelineItem(
            id=incident.id,
            ts=iso_utc(incident.detected_at),
            type=incident.category,
            severity=incident.state,
            client_id=incident.client_id,
            summary=incident.summary,
        )
        for incident in overview_repo.recent_incidents(db, limit=20)
    ]
    flapping_counts = overview_repo.flapping_client_counts(
        db,
        since=now - timedelta(seconds=effective.flapping_window_seconds),
        categories=(CATEGORY_AGENT_DOWN, CATEGORY_HOST_OR_NET_DOWN),
    )
    return OverviewResponse(
        clients_total=clients_total,
        clients_online=clients_online,
        clients_down=max(0, clients_total - clients_online),
        incidents_open=incidents_open,
        open_incidents=incidents_open,
        incidents_24h=incidents_24h,
        flapping_clients=sum(
            1 for _, count in flapping_counts if count >= effective.flapping_threshold
        ),
        timeline=timeline,
        generated_at=iso_utc(now),
        server_time=int(time.time()),
    )


def list_clients(db: Session, settings: Settings) -> ClientListResponse:
    now = utcnow()
    effective = effective_settings(db, settings)
    clients = client_repo.list_clients(db)
    latest = incident_repo.latest_by_client(db, [client.client_id for client in clients])
    return ClientListResponse(
        items=[
            serializers.client_summary(
                client, effective, now, latest.get(client.client_id)
            )
            for client in clients
        ]
    )


def get_client_detail(
    db: Session, settings: Settings, client_id: str
) -> ClientDetailResponse:
    client = client_repo.get_client(db, client_id)
    if client is None:
        raise NotFoundError("客户端不存在")

    now = utcnow()
    effective = effective_settings(db, settings)
    heartbeats = heartbeat_repo.list_for_client(db, client_id)
    latest_heartbeat = heartbeats[0] if heartbeats else None
    latest_incident = incident_repo.latest_by_client(db, [client_id]).get(client_id)

    return ClientDetailResponse(
        client=serializers.client_summary(client, effective, now, latest_incident),
        modules=serializers.modules_from_heartbeat(latest_heartbeat),
        guards=serializers.guards_from_heartbeat(latest_heartbeat),
        heartbeats=[serializers.heartbeat_item(item) for item in heartbeats],
        incidents=[
            serializers.incident_item(incident)
            for incident in incident_repo.list_for_client(db, client_id)
        ],
        recent_events=[
            serializers.event_item(event)
            for event in event_repo.list_for_client(db, client_id)
        ],
        actions=[],
        generated_at=iso_utc(now),
    )


def list_incidents(
    db: Session,
    *,
    client_id: str | None = None,
    state: str | None = None,
    category: str | None = None,
    limit: int = 100,
) -> IncidentListResponse:
    rows = incident_repo.list_incidents(
        db, client_id=client_id, state=state, category=category, limit=limit
    )
    return IncidentListResponse(
        items=[serializers.incident_item(incident) for incident in rows]
    )
