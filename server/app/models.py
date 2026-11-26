"""数据模型：最小集合，SQLite 存储，时间统一为 naive UTC。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    secret_sha256: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(64), default="")
    fw_version: Mapped[str] = mapped_column(String(64), default="")
    agent_version: Mapped[str] = mapped_column(String(32), default="")
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_beacon: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    boot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wan_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Beacon(Base):
    __tablename__ = "beacons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime)
    uptime: Mapped[int] = mapped_column(Integer, default=0)
    boot_id: Mapped[str] = mapped_column(String(64), default="")


class Heartbeat(Base):
    __tablename__ = "heartbeats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    uptime: Mapped[int] = mapped_column(Integer, default=0)
    boot_id: Mapped[str] = mapped_column(String(64), default="")
    wan_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    modules_json: Mapped[str] = mapped_column(Text, default="{}")
    guards_json: Mapped[str] = mapped_column(Text, default="{}")


class GuardConfig(Base):
    """服务端下发的守护项配置（与客户端本地配置解耦）。"""

    __tablename__ = "guards"

    # 复合主键：同一客户端内 guard_id 唯一
    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    guard_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    template: Mapped[str] = mapped_column(String(64), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 完整 Guard 配置 JSON（id/name/template/enabled/checks/policy/recovery/limits/evaluate）
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    config_hash: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ClientEvent(Base):
    __tablename__ = "client_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), index=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    type: Mapped[str] = mapped_column(String(64), default="")
    domain: Mapped[str] = mapped_column(String(64), default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    received_at: Mapped[datetime] = mapped_column(DateTime)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), index=True)
    guard_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    guard_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    category: Mapped[str] = mapped_column(String(64))
    # SUSPECT / DOWN / RECOVERED
    state: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    detected_at: Mapped[datetime] = mapped_column(DateTime)
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    client_id: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(String(256), default="")
    # down / recovery / system / test
    kind: Mapped[str] = mapped_column(String(16), default="down")
    # pending / sent / failed / skipped / dead（重试耗尽）
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Nonce(Base):
    __tablename__ = "nonces"

    nonce: Mapped[str] = mapped_column(String(128), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), index=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(128), default="")
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    detail: Mapped[str] = mapped_column(Text, default="")
