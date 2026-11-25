"""状态机：心跳/beacon 缺失判定与 incident 生命周期。

核心判定均为纯函数，`now` 由调用方注入，便于单测。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Client, Heartbeat, Incident
from app.settings import Thresholds

CATEGORY_AGENT_DOWN = "agent_down"
CATEGORY_HOST_OR_NET_DOWN = "host_or_net_down"
CATEGORY_GUARD_FAILED = "guard_failed"

SILENCE_CATEGORIES = (CATEGORY_AGENT_DOWN, CATEGORY_HOST_OR_NET_DOWN)
GUARD_ALERT_STATES = ("FAILED", "GAVE_UP")

STATE_SUSPECT = "SUSPECT"
STATE_DOWN = "DOWN"
STATE_RECOVERED = "RECOVERED"


class SilenceState(str, Enum):
    """客户端静默判定结果。"""

    HEALTHY = "healthy"
    SUSPECT = "suspect"
    AGENT_DOWN = "agent_down"
    HOST_OR_NET_DOWN = "host_or_net_down"


@dataclass(frozen=True)
class SignalSnapshot:
    last_seen: datetime | None
    last_beacon: datetime | None
    boot_id: str | None = None


@dataclass(frozen=True)
class Evaluation:
    state: SilenceState
    category: str | None
    heartbeat_age: float | None
    beacon_age: float | None

    @property
    def is_down(self) -> bool:
        return self.state in (SilenceState.AGENT_DOWN, SilenceState.HOST_OR_NET_DOWN)


def _age_seconds(now: datetime, ts: datetime | None) -> float | None:
    if ts is None:
        return None
    return (now - ts).total_seconds()


def evaluate(now: datetime, snapshot: SignalSnapshot, thresholds: Thresholds) -> Evaluation:
    """根据最近一次心跳/beacon 的静默时长判定状态与归因。

    - 心跳 ≤ suspect：HEALTHY
    - 心跳 > suspect 但 ≤ down：SUSPECT
    - 心跳 > down：beacon 新鲜 → AGENT_DOWN，beacon 也超时 → HOST_OR_NET_DOWN
    - 从未收到心跳：无数据，按 HEALTHY 处理（避免注册即告警）
    """
    heartbeat_age = _age_seconds(now, snapshot.last_seen)
    beacon_age = _age_seconds(now, snapshot.last_beacon)

    if heartbeat_age is None:
        return Evaluation(SilenceState.HEALTHY, None, None, beacon_age)

    beacon_fresh = beacon_age is not None and beacon_age <= thresholds.beacon_seconds
    category = CATEGORY_AGENT_DOWN if beacon_fresh else CATEGORY_HOST_OR_NET_DOWN

    if heartbeat_age <= thresholds.suspect_seconds:
        return Evaluation(SilenceState.HEALTHY, None, heartbeat_age, beacon_age)
    if heartbeat_age <= thresholds.down_seconds:
        return Evaluation(SilenceState.SUSPECT, category, heartbeat_age, beacon_age)
    state = SilenceState.AGENT_DOWN if beacon_fresh else SilenceState.HOST_OR_NET_DOWN
    return Evaluation(state, category, heartbeat_age, beacon_age)


def is_flapping(
    recovered_times: Sequence[datetime | None],
    now: datetime,
    window_seconds: int = 1800,
    threshold: int = 3,
) -> bool:
    """窗口内 RECOVERED 次数达到阈值即视为抖动。"""
    since = now - timedelta(seconds=window_seconds)
    count = sum(1 for ts in recovered_times if ts is not None and ts >= since)
    return count >= threshold


def incident_state_for(evaluation: Evaluation) -> str:
    return STATE_DOWN if evaluation.is_down else STATE_SUSPECT


def _summary_for(evaluation: Evaluation) -> str:
    hb = "-" if evaluation.heartbeat_age is None else f"{int(evaluation.heartbeat_age)}s"
    beacon = "-" if evaluation.beacon_age is None else f"{int(evaluation.beacon_age)}s"
    return f"{evaluation.category}: 心跳缺失 {hb}，beacon 缺失 {beacon}"


def _append_summary(summary: str | None, note: str) -> str:
    return f"{summary}; {note}" if summary else note


def get_open_incident(db: Session, client_id: str) -> Incident | None:
    stmt = (
        select(Incident)
        .where(
            Incident.client_id == client_id,
            Incident.category.in_(SILENCE_CATEGORIES),
            Incident.recovered_at.is_(None),
        )
        .order_by(Incident.started_at.asc())
    )
    return db.scalars(stmt).first()


def record_silence(
    db: Session,
    client_id: str,
    evaluation: Evaluation,
    now: datetime,
    started_at: datetime,
) -> Incident | None:
    """按判定结果创建或推进 incident；同一客户端只保留一个 open incident。"""
    if evaluation.category is None:
        return None

    incident = get_open_incident(db, client_id)
    state = incident_state_for(evaluation)
    if incident is None:
        incident = Incident(
            client_id=client_id,
            category=evaluation.category,
            state=state,
            started_at=started_at,
            detected_at=now,
            summary=_summary_for(evaluation),
            evidence_json="{}",
        )
        db.add(incident)
    else:
        # 归因可能从 agent_down 升级为 host_or_net_down，随状态推进更新
        incident.category = evaluation.category
        incident.state = state
        incident.summary = _summary_for(evaluation)
    db.flush()
    return incident


def recover_incidents(
    db: Session,
    client_id: str,
    now: datetime,
    *,
    note: str = "心跳恢复",
    evidence: dict[str, object] | None = None,
    flapping_window_seconds: int = 1800,
    flapping_threshold: int = 3,
) -> list[Incident]:
    """将客户端所有 open incident 置为 RECOVERED，并标记 flapping。"""
    open_incidents = list(
        db.scalars(
            select(Incident).where(
                Incident.client_id == client_id,
                Incident.category.in_(SILENCE_CATEGORIES),
                Incident.recovered_at.is_(None),
            )
        ).all()
    )
    if not open_incidents:
        return []

    for incident in open_incidents:
        incident.state = STATE_RECOVERED
        incident.recovered_at = now
        incident.summary = _append_summary(incident.summary, f"recovered: {note}")
        if evidence:
            incident.evidence_json = json.dumps(evidence, ensure_ascii=False)
    db.flush()

    recovered_times = list(
        db.scalars(
            select(Incident.recovered_at).where(
                Incident.client_id == client_id,
                Incident.category.in_(SILENCE_CATEGORIES),
                Incident.recovered_at.is_not(None),
            )
        ).all()
    )
    if is_flapping(recovered_times, now, flapping_window_seconds, flapping_threshold):
        for incident in open_incidents:
            incident.summary = _append_summary(incident.summary, "flapping")
    return open_incidents


def scan_once(
    db: Session,
    now: datetime,
    thresholds: Thresholds,
    *,
    flapping_window_seconds: int = 1800,
    flapping_threshold: int = 3,
) -> list[Incident]:
    """扫描全部启用客户端，推进 incident 状态。worker 每个周期调用一次。"""
    touched: list[Incident] = []
    clients = db.scalars(select(Client).where(Client.enabled.is_(True))).all()
    for client in clients:
        snapshot = SignalSnapshot(client.last_seen, client.last_beacon, client.boot_id)
        evaluation = evaluate(now, snapshot, thresholds)
        if evaluation.state is SilenceState.HEALTHY:
            touched.extend(
                recover_incidents(
                    db,
                    client.client_id,
                    now,
                    note="服务端扫描确认心跳恢复",
                    evidence={"recovered_by": "server_scan"},
                    flapping_window_seconds=flapping_window_seconds,
                    flapping_threshold=flapping_threshold,
                )
            )
            continue
        incident = record_silence(
            db,
            client.client_id,
            evaluation,
            now,
            started_at=client.last_seen or client.created_at,
        )
        if incident is not None:
            touched.append(incident)
    db.flush()
    return touched


def _latest_heartbeat(db: Session, client_id: str) -> Heartbeat | None:
    stmt = (
        select(Heartbeat)
        .where(Heartbeat.client_id == client_id)
        .order_by(Heartbeat.received_at.desc(), Heartbeat.id.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def _guard_summary(guard: dict, status: str) -> str:
    name = guard.get("name") or guard.get("id") or "-"
    check_status = guard.get("check_status") or "-"
    failures = guard.get("failure_count")
    parts = [f"守护项 {name} 状态 {status}", f"检测结果 {check_status}"]
    if isinstance(failures, int) and not isinstance(failures, bool):
        parts.append(f"连续失败 {failures} 次")
    return "；".join(parts)


def scan_guards(db: Session, now: datetime) -> list[Incident]:
    """根据心跳里的 guards 快照推进 Guard 级 incident。

    - FAILED / GAVE_UP：创建或保持 DOWN，触发一次告警
    - OK / DISABLED：关闭该 Guard 的 open incident，触发恢复通知
    - PENDING / RECOVERING：不新建告警，避免抖动噪音
    """
    touched: list[Incident] = []
    clients = db.scalars(select(Client).where(Client.enabled.is_(True))).all()
    for client in clients:
        heartbeat = _latest_heartbeat(db, client.client_id)
        if heartbeat is None:
            continue
        try:
            guards = json.loads(heartbeat.guards_json or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(guards, dict):
            continue

        for guard_id, guard in guards.items():
            if not isinstance(guard, dict):
                continue
            guard_id = str(guard_id)
            name = str(guard.get("name") or guard_id)
            status = str(guard.get("status") or "")
            open_incident = db.scalars(
                select(Incident).where(
                    Incident.client_id == client.client_id,
                    Incident.guard_id == guard_id,
                    Incident.recovered_at.is_(None),
                )
            ).first()

            if status in GUARD_ALERT_STATES:
                evidence = json.dumps(
                    {
                        "guard": guard,
                        "heartbeat_seq": heartbeat.seq,
                        "heartbeat_at": heartbeat.received_at.isoformat(),
                    },
                    ensure_ascii=False,
                )
                summary = _guard_summary(guard, status)
                if open_incident is None:
                    open_incident = Incident(
                        client_id=client.client_id,
                        guard_id=guard_id,
                        guard_name=name,
                        category=CATEGORY_GUARD_FAILED,
                        state=STATE_DOWN,
                        started_at=now,
                        detected_at=now,
                        summary=summary,
                        evidence_json=evidence,
                    )
                    db.add(open_incident)
                else:
                    open_incident.state = STATE_DOWN
                    open_incident.guard_name = name
                    open_incident.summary = summary
                    open_incident.evidence_json = evidence
                touched.append(open_incident)
            elif status in ("OK", "DISABLED") and open_incident is not None:
                open_incident.state = STATE_RECOVERED
                open_incident.recovered_at = now
                open_incident.summary = _append_summary(
                    open_incident.summary, f"recovered: 守护项 {name} 恢复"
                )
                touched.append(open_incident)
    db.flush()
    return touched
