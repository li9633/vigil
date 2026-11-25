"""客户端接入 API：注册、beacon、heartbeat、events、config。"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session, utcnow
from app.models import Beacon, Client, ClientEvent, GuardConfig, Heartbeat
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
    authenticate_client,
    hash_secret,
    new_client_id,
    new_client_secret,
    verify_bootstrap,
)
from app.settings import Settings, settings_from_app
from app.state_machine import recover_incidents

router = APIRouter(prefix="/api/v1", tags=["client"])

SUPPORTED_PROTOCOL = 1


def load_guard_configs(db: Session, client_id: str) -> list[dict[str, Any]]:
    """读取该客户端在服务端配置的 Guard，按 guard_id 排序。"""
    rows = db.scalars(
        select(GuardConfig)
        .where(GuardConfig.client_id == client_id)
        .order_by(GuardConfig.guard_id)
    ).all()
    items: list[dict[str, Any]] = []
    for row in rows:
        try:
            config = json.loads(row.config_json or "{}")
        except json.JSONDecodeError:
            continue
        if isinstance(config, dict):
            items.append(config)
    return items


def build_policy(
    settings: Settings, guards: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """下发给客户端的策略配置；仅在存在服务端 Guard 时附带 guards。"""
    policy: dict[str, Any] = {
        "schema_version": 1,
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
            "cooldown_seconds": 300,
            "max_per_hour": 3,
        },
        "config_poll_interval": 300,
    }
    if guards:
        policy["guards"] = guards
    return policy


def policy_etag(settings: Settings, guards: list[dict[str, Any]] | None = None) -> str:
    body = json.dumps(
        build_policy(settings, guards), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


def require_client(
    request: Request,
    x_vigil_client: Annotated[str | None, Header(alias="X-Vigil-Client")] = None,
    authorization: Annotated[str | None, Header()] = None,
    x_vigil_timestamp: Annotated[str | None, Header(alias="X-Vigil-Timestamp")] = None,
    x_vigil_nonce: Annotated[str | None, Header(alias="X-Vigil-Nonce")] = None,
    db: Session = Depends(get_session),
) -> Client:
    settings: Settings = request.app.state.settings
    return authenticate_client(
        db,
        settings,
        client_id=x_vigil_client,
        authorization=authorization,
        timestamp=x_vigil_timestamp,
        nonce=x_vigil_nonce,
        now=utcnow(),
    )


def _ensure_body_client(client: Client, body_client_id: str) -> None:
    if body_client_id != client.client_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="body 中的 client_id 与鉴权头不一致",
        )


def _record_reboot_event(
    db: Session, client: Client, new_boot_id: str, seq: int, now: datetime
) -> None:
    """boot_id 变化且此前心跳中断：记录 device_reboot 事件。"""
    db.add(
        ClientEvent(
            client_id=client.client_id,
            event_id=str(uuid.uuid4()),
            seq=seq,
            type="device_reboot",
            domain="agent",
            payload_json=json.dumps(
                {"previous_boot_id": client.boot_id, "boot_id": new_boot_id},
                ensure_ascii=False,
            ),
            received_at=now,
        )
    )


@router.post("/register", response_model=RegisterResponse)
def register(
    payload: RegisterRequest,
    x_vigil_bootstrap: Annotated[str | None, Header(alias="X-Vigil-Bootstrap")] = None,
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
) -> RegisterResponse:
    verify_bootstrap(x_vigil_bootstrap, settings.bootstrap_token)
    if payload.protocol != SUPPORTED_PROTOCOL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的协议版本: {payload.protocol}",
        )

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

    return RegisterResponse(
        client_id=client.client_id,
        secret=secret,
        server_time=int(time.time()),
        heartbeat_interval=settings.heartbeat_interval,
    )


@router.post("/beacon", response_model=BeaconResponse)
def beacon(
    payload: BeaconRequest,
    db: Session = Depends(get_session),
    client: Client = Depends(require_client),
) -> BeaconResponse:
    _ensure_body_client(client, payload.client_id)
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


@router.post("/heartbeat", response_model=HeartbeatResponse)
def heartbeat(
    payload: HeartbeatRequest,
    db: Session = Depends(get_session),
    client: Client = Depends(require_client),
    settings: Settings = Depends(settings_from_app),
) -> HeartbeatResponse:
    _ensure_body_client(client, payload.client_id)
    now = utcnow()

    previous_boot_id = client.boot_id
    silent_seconds = (
        None if client.last_seen is None else (now - client.last_seen).total_seconds()
    )
    reboot_detected = (
        previous_boot_id is not None
        and payload.boot_id != previous_boot_id
        and silent_seconds is not None
        and silent_seconds > settings.suspect_seconds
    )

    if reboot_detected:
        _record_reboot_event(db, client, payload.boot_id, payload.seq, now)

    client.last_seen = now
    client.boot_id = payload.boot_id
    if payload.wan_ip:
        client.wan_ip = payload.wan_ip
    if payload.agent_version:
        client.agent_version = payload.agent_version

    db.add(
        Heartbeat(
            client_id=client.client_id,
            received_at=now,
            seq=payload.seq,
            uptime=payload.uptime,
            boot_id=payload.boot_id,
            wan_ip=payload.wan_ip,
            modules_json=json.dumps(payload.modules, ensure_ascii=False),
            guards_json=json.dumps(payload.guards, ensure_ascii=False),
        )
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
        flapping_window_seconds=settings.flapping_window_seconds,
        flapping_threshold=settings.flapping_threshold,
    )
    db.commit()

    return HeartbeatResponse(
        server_time=int(time.time()),
        config_etag=policy_etag(settings, load_guard_configs(db, client.client_id)),
        actions_enabled=settings.actions_enabled,
    )


@router.post("/events", response_model=EventsResponse)
def submit_events(
    payload: EventsRequest,
    db: Session = Depends(get_session),
    client: Client = Depends(require_client),
) -> EventsResponse:
    _ensure_body_client(client, payload.client_id)
    now = utcnow()

    event_ids = [event.event_id for event in payload.events]
    known: set[str] = set()
    if event_ids:
        known = set(
            db.scalars(
                select(ClientEvent.event_id).where(ClientEvent.event_id.in_(event_ids))
            ).all()
        )

    for event in payload.events:
        if event.event_id in known:
            continue
        known.add(event.event_id)
        db.add(
            ClientEvent(
                client_id=client.client_id,
                event_id=event.event_id,
                seq=event.seq,
                type=event.type,
                domain=event.domain,
                payload_json=json.dumps(event.payload, ensure_ascii=False),
                received_at=now,
            )
        )
    db.commit()
    return EventsResponse(acked=event_ids)


@router.get("/config")
def get_config(
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_from_app),
    client: Client = Depends(require_client),
) -> Response:
    guards = load_guard_configs(db, client.client_id)
    policy = build_policy(settings, guards)
    etag = policy_etag(settings, guards)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
    return JSONResponse(content=policy, headers={"ETag": etag})
