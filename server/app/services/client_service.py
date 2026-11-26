"""客户端接入用例：注册、beacon、heartbeat、事件上报与策略下发。

本层负责事务边界；HTTP 相关处理留在 api/client 层。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain.client_policy import build_policy, policy_etag
from app.errors import UnauthorizedError, ValidationError
from app.logstore import log_event
from app.models import Beacon, Client
from app.policies import effective_settings
from app.repositories import event_repo, guard_repo, heartbeat_repo
from app.schemas import (
    BeaconRequest,
    BeaconResponse,
    EventsRequest,
    EventsResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    RegisterRequest,
    RegisterResponse,
)
from app.security import (
    hash_secret,
    new_client_id,
    new_client_secret,
    verify_bootstrap,
)
from app.settings import Settings
from app.state_machine import recover_incidents

SUPPORTED_PROTOCOL = 1


def _ensure_same_client(client: Client, body_client_id: str) -> None:
    if body_client_id != client.client_id:
        raise UnauthorizedError("body 中的 client_id 与鉴权头不一致")


def register_client(
    db: Session,
    settings: Settings,
    *,
    bootstrap_token: str | None,
    payload: RegisterRequest,
) -> RegisterResponse:
    verify_bootstrap(bootstrap_token, settings.bootstrap_token)
    if payload.protocol != SUPPORTED_PROTOCOL:
        raise ValidationError(f"不支持的协议版本: {payload.protocol}")

    secret = new_client_secret()
    client = Client(
        client_id=new_client_id(),
        name=payload.name,
        secret_sha256=hash_secret(secret),
        model=payload.model,
        fw_version=payload.fw_version,
        agent_version=payload.agent_version,
    )
    db.add(client)
    db.commit()
    log_event(
        "info",
        "client.register",
        "客户端注册成功",
        client_id=client.client_id,
        name=client.name,
        model=client.model,
        agent_version=payload.agent_version,
    )
    return RegisterResponse(
        client_id=client.client_id,
        secret=secret,
        server_time=int(time.time()),
        heartbeat_interval=effective_settings(db, settings).heartbeat_interval,
    )


def record_beacon(db: Session, client: Client, payload: BeaconRequest) -> BeaconResponse:
    _ensure_same_client(client, payload.client_id)
    now = utcnow()
    client.last_beacon = now
    if payload.boot_id:
        client.boot_id = payload.boot_id
    db.add(
        Beacon(
            client_id=client.client_id,
            received_at=now,
            uptime=payload.uptime,
            boot_id=payload.boot_id,
        )
    )
    db.commit()
    return BeaconResponse(server_time=int(time.time()))


def _record_reboot_event(
    db: Session, client: Client, new_boot_id: str, seq: int
) -> None:
    event_repo.add(
        db,
        client_id=client.client_id,
        event_id=str(uuid.uuid4()),
        seq=seq,
        event_type="device_reboot",
        domain="agent",
        payload={"previous_boot_id": client.boot_id, "boot_id": new_boot_id},
        received_at=utcnow(),
    )


def record_heartbeat(
    db: Session,
    client: Client,
    payload: HeartbeatRequest,
    settings: Settings,
) -> HeartbeatResponse:
    _ensure_same_client(client, payload.client_id)
    now = utcnow()
    effective = effective_settings(db, settings)

    previous_boot_id = client.boot_id
    silent_seconds = (
        None if client.last_seen is None else (now - client.last_seen).total_seconds()
    )
    reboot_detected = (
        previous_boot_id is not None
        and payload.boot_id != previous_boot_id
        and silent_seconds is not None
        and silent_seconds > effective.suspect_seconds
    )

    if reboot_detected:
        _record_reboot_event(db, client, payload.boot_id, payload.seq)

    client.last_seen = now
    client.boot_id = payload.boot_id
    if payload.wan_ip:
        client.wan_ip = payload.wan_ip
    if payload.agent_version:
        client.agent_version = payload.agent_version

    heartbeat_repo.add(
        db,
        client_id=client.client_id,
        received_at=now,
        seq=payload.seq,
        uptime=payload.uptime,
        boot_id=payload.boot_id,
        wan_ip=payload.wan_ip,
        modules=payload.modules,
        guards=payload.guards,
    )

    evidence: dict[str, object] | None = None
    if reboot_detected:
        evidence = {
            "boot_id_changed": True,
            "previous_boot_id": previous_boot_id,
            "boot_id": payload.boot_id,
        }
    recover_incidents(
        db,
        client.client_id,
        now,
        note="设备重启后恢复" if reboot_detected else "心跳恢复",
        evidence=evidence,
        flapping_window_seconds=effective.flapping_window_seconds,
        flapping_threshold=effective.flapping_threshold,
    )
    db.commit()

    guards = guard_repo.load_guard_configs(db, client.client_id)
    return HeartbeatResponse(
        server_time=int(time.time()),
        config_etag=policy_etag(effective, guards),
        actions_enabled=effective.actions_enabled,
    )


def submit_events(
    db: Session, client: Client, payload: EventsRequest
) -> EventsResponse:
    _ensure_same_client(client, payload.client_id)
    now = utcnow()

    event_ids = [event.event_id for event in payload.events]
    known = event_repo.known_event_ids(db, event_ids)

    for event in payload.events:
        if event.event_id in known:
            continue
        known.add(event.event_id)
        event_repo.add(
            db,
            client_id=client.client_id,
            event_id=event.event_id,
            seq=event.seq,
            event_type=event.type,
            domain=event.domain,
            payload=event.payload,
            received_at=now,
        )
    db.commit()
    return EventsResponse(acked=event_ids)


def load_policy(
    db: Session, client: Client, settings: Settings
) -> tuple[dict[str, Any], str]:
    """返回 (策略文档, ETag)，供 /config 与心跳响应复用。"""
    effective = effective_settings(db, settings)
    guards = guard_repo.load_guard_configs(db, client.client_id)
    return build_policy(effective, guards), policy_etag(effective, guards)
