"""状态机 worker：独立进程运行 `python -m app.worker`。"""

from __future__ import annotations

import logging
import time

from datetime import timedelta

from app.db import init_db, init_engine, session_scope, utcnow
from app.notifications import (
    load_config,
    pending_recovery_incidents,
    retry_failed,
    send_incident,
)
from app.security import purge_expired_nonces
from app.settings import Settings, get_settings
from app.state_machine import STATE_DOWN, STATE_RECOVERED, scan_once

logger = logging.getLogger(__name__)


def run_forever(settings: Settings) -> None:
    """按配置间隔循环扫描，单次异常不中断进程。"""
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
                purged = purge_expired_nonces(db, now, settings.nonce_ttl_seconds)
                _dispatch_notifications(db, touched, now)
                if touched:
                    logger.info(
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
                send_incident(db, incident, kind, now, config)
            except Exception:
                logger.exception("通知发送失败：incident=%s kind=%s", incident.id, kind)
        for incident in pending_recovery_incidents(db, now - timedelta(hours=24)):
            try:
                send_incident(db, incident, "recovery", now, config)
            except Exception:
                logger.exception("恢复通知发送失败：incident=%s", incident.id)
        retry_failed(db, now, config)
    except Exception:
        logger.exception("通知分发失败")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()
    init_engine(settings.database_url)
    init_db()
    logger.info(
        "worker 启动：interval=%ss suspect=%ss down=%ss beacon=%ss",
        settings.worker_interval_seconds,
        settings.suspect_seconds,
        settings.down_seconds,
        settings.beacon_seconds,
    )
    run_forever(settings)


if __name__ == "__main__":
    main()
