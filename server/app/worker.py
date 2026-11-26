"""状态机 worker：独立进程运行 `python -m app.worker`。"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from app.db import init_db, init_engine, session_scope, utcnow
from app.diskguard import check_disk, disk_path
from app.logstore import configure_logging, log_event
from app.notifications import (
    load_config,
    pending_recovery_incidents,
    retry_failed,
    send_incident,
)
from app.retention import purge_expired
from app.security import purge_expired_nonces
from app.settings import Settings, get_settings
from app.state_machine import STATE_DOWN, STATE_RECOVERED, scan_guards, scan_once

logger = logging.getLogger(__name__)


def run_forever(settings: Settings) -> None:
    """按配置间隔循环扫描，单次异常不中断进程。"""
    last_purge = 0.0
    last_disk_check = 0.0
    while True:
        try:
            with session_scope() as db:
                now = utcnow()
                touched = scan_once(
                    db,
                    now,
                    settings.thresholds,
                    flapping_window_seconds=settings.flapping_window_seconds,
                    flapping_threshold=settings.flapping_threshold,
                )
                touched.extend(scan_guards(db, now))
                purged = purge_expired_nonces(db, now, settings.nonce_ttl_seconds)
                if time.monotonic() - last_purge >= 3600:
                    removed = purge_expired(db, now, settings.retention_days)
                    last_purge = time.monotonic()
                    if sum(removed.values()):
                        log_event(
                            "info",
                            "retention.purge",
                            "清理过期明细数据",
                            retention_days=settings.retention_days,
                            **removed,
                        )
                if time.monotonic() - last_disk_check >= settings.disk_check_interval_seconds:
                    try:
                        check_disk(
                            db,
                            now,
                            disk_path(settings.database_url),
                            settings.disk_alert_percent,
                            settings.disk_alert_cooldown_seconds,
                        )
                    except Exception:
                        logger.exception("磁盘水位检查失败")
                    last_disk_check = time.monotonic()
                _dispatch_notifications(db, touched, now)
                if touched:
                    logger.debug(
                        "扫描完成：更新 %d 个 incident，清理 %d 条过期 nonce",
                        len(touched),
                        purged,
                    )
        except Exception:
            logger.exception("worker 扫描失败")
        time.sleep(settings.worker_interval_seconds)


def _dispatch_notifications(db, touched, now) -> None:
    """发送 DOWN / RECOVERY 通知并重试失败记录；任何异常都不影响 worker 主循环。"""
    try:
        config = load_config(db)
        for incident in touched:
            if incident.state == STATE_DOWN:
                kind = "down"
            elif incident.state == STATE_RECOVERED:
                kind = "recovery"
            else:
                continue
            try:
                records = send_incident(db, incident, kind, now, config)
                for record in records:
                    if record.status == "sent":
                        log_event(
                            "info",
                            "notify.send",
                            "通知已发送",
                            incident_id=incident.id,
                            client_id=incident.client_id,
                            channel=record.channel,
                            kind=record.kind,
                        )
                    elif record.status == "failed":
                        log_event(
                            "error",
                            "notify.fail",
                            "通知发送失败",
                            incident_id=incident.id,
                            client_id=incident.client_id,
                            channel=record.channel,
                            kind=record.kind,
                            error=record.last_error,
                        )
            except Exception:
                logger.exception("通知发送失败：incident=%s kind=%s", incident.id, kind)
                log_event(
                    "error",
                    "notify.fail",
                    "通知分发异常",
                    incident_id=incident.id,
                    client_id=incident.client_id,
                    kind=kind,
                )
        for incident in pending_recovery_incidents(db, now - timedelta(hours=24)):
            try:
                send_incident(db, incident, "recovery", now, config)
            except Exception:
                logger.exception("恢复通知发送失败：incident=%s", incident.id)
                log_event(
                    "error",
                    "notify.fail",
                    "恢复通知发送异常",
                    incident_id=incident.id,
                    client_id=incident.client_id,
                    kind="recovery",
                )
        retry_failed(db, now, config)
    except Exception:
        logger.exception("通知分发失败")
        log_event("error", "notify.fail", "通知分发失败")


def main() -> None:
    settings = get_settings()
    configure_logging("worker", settings)
    init_engine(settings.database_url)
    init_db()
    log_event(
        "info",
        "app.start",
        "Vigil worker 启动",
        interval_seconds=settings.worker_interval_seconds,
        suspect_seconds=settings.suspect_seconds,
        down_seconds=settings.down_seconds,
        beacon_seconds=settings.beacon_seconds,
    )
    run_forever(settings)


if __name__ == "__main__":
    main()
