"""上报编排：注册、心跳、事件补传与配置拉取。"""

import time

from ..logstore import log_event, set_log_context
from ..runtime import read_first_line, read_fw_version, read_uptime
from . import guard_service, views

REGISTER_RETRY_SECONDS = 300


def acked_seqs(batch, acked):
    """把服务端 ack（event_id 字符串或 seq 整数）映射为本地 seq 列表。

    服务端 /api/v1/events 返回 acked=event_id 列表；旧版本可能返回 seq。
    无法识别的 ack 不确认，等待下轮重试。
    """
    if not isinstance(acked, list) or not acked:
        return [item.get("seq") for item in batch]
    by_id = {str(item.get("event_id")): item.get("seq") for item in batch}
    seqs = []
    for value in acked:
        if isinstance(value, int) and not isinstance(value, bool):
            seqs.append(value)
        elif str(value) in by_id:
            seqs.append(by_id[str(value)])
    return seqs


def ensure_registered(app):
    """确保已注册；失败按固定间隔重试。返回是否已注册。"""
    if app.transport.is_registered:
        return True
    now = time.time()
    if now < app._next_register_at:
        return False
    server = app.config.get("server") or {}
    if not server.get("base_url") or not server.get("bootstrap_token"):
        app._next_register_at = now + REGISTER_RETRY_SECONDS
        return False

    result = app.transport.register(
        app.agent_name(),
        model=read_first_line("/tmp/sysinfo/model"),
        fw_version=read_fw_version(),
    )
    if result.get("ok"):
        app._next_register_at = 0.0
        app.scheduler.update_interval(
            "heartbeat", int(app.config["server"]["heartbeat_interval"])
        )
        app._record_event(
            "agent_registered",
            "agent",
            {"client_id": app.transport.client_id},
        )
        set_log_context(app.transport.client_id, app.transport.boot_id)
        log_event(
            "info",
            "client.register",
            "注册成功",
            client_id=app.transport.client_id,
        )
        return True

    app._next_register_at = now + REGISTER_RETRY_SECONDS
    app._log_throttled(
        "register",
        "warn",
        "client.register.fail",
        "注册失败，5 分钟后重试",
        interval=REGISTER_RETRY_SECONDS,
        error=str(result.get("error") or ""),
    )
    return False


def heartbeat_once(app):
    ensure_registered(app)
    if not app.transport.is_registered:
        return
    checks = app.state.snapshot_checks()
    stats = app.spool.stats()
    result = app.transport.heartbeat(
        app.spool.next_seq(persist=False),
        read_uptime(),
        checks=views.checks_snapshot(app, checks),
        probes=app.registry.catalog(),
        guards=guard_service.heartbeat_payload(app.state.snapshot_guards()),
        spool={"pending": stats["pending"], "dropped": stats["dropped"]},
        wan_ip=views.wan_ip(app, checks),
    )
    if result.get("ok"):
        app.state.last_heartbeat = time.time()
        flush_spool(app)
    else:
        app._log_throttled(
            "heartbeat",
            "warn",
            "heartbeat.fail",
            "心跳失败",
            interval=60,
            error=str(result.get("error") or ""),
        )


def flush_spool(app):
    """按批补传 spool 事件，失败即停止等待下一轮。"""
    for _ in range(3):
        batch = app.spool.pending(50)
        if not batch:
            return
        result = app.transport.events(batch)
        if not result.get("ok"):
            app._log_throttled(
                "events",
                "warn",
                "events.upload.fail",
                "事件补传失败",
                interval=60,
                error=str(result.get("error") or ""),
            )
            return
        body = result.get("body") or {}
        acked = body.get("acked") or body.get("acked_seqs")
        app.spool.ack(acked_seqs(batch, acked))


def poll_config(app):
    result = app.transport.fetch_config(app._etag)
    if not result.get("ok"):
        app._log_throttled(
            "config",
            "warn",
            "config.fetch.fail",
            "拉取服务端配置失败",
            interval=60,
            error=str(result.get("error") or ""),
        )
        return
    if result.get("status") == 304:
        return
    if result.get("etag"):
        app._etag = result["etag"]
    body = result.get("body") or {}
    app._server_config = body if isinstance(body, dict) else {}
    apply_server_config(app, app._server_config)
    app._record_event(
        "config_received",
        "agent",
        {"etag": app._etag or "", "keys": sorted(app._server_config.keys())[:20]},
    )
    log_event(
        "info",
        "config.received",
        "已接收服务端配置",
        keys=len(app._server_config),
        etag=app._etag or "",
    )


def apply_server_config(app, server_config):
    """应用服务端下发的 checks + guards（整体替换、热更新）。

    - 含 checks / guards：校验通过后整体切换到 server 来源，本地只读
    - 两者都未下发：回退到本地配置
    - 校验失败：只记日志并保持当前配置，不做部分应用
    """
    decision, errors = guard_service.plan_server_config(
        server_config,
        app._local_checks,
        app.config.get("guards") or [],
        app.checks,
        app.guards,
        app.guards_source,
        app._server_guards,
        app.registry,
    )
    if errors:
        log_event(
            "warn",
            "guard_config.invalid",
            "服务端下发的配置非法，保持当前配置",
            errors=errors[:20],
        )
        return
    if decision is None:
        return

    # checks / guards 替换与 detect_once 互斥，避免检测过程中被换掉
    with app._detect_lock:
        app.checks = decision["checks"]
        app.guards = decision["guards"]
        app.guards_source = decision["source"]
        app._server_guards = decision["server_guards"]
        app._guard_last_run.clear()
        app.detect_interval = guard_service.detect_loop_interval(app.guards)
        app.scheduler.update_interval("detect", app.detect_interval)
    log_event(
        "info",
        "guard_config.apply",
        "已应用服务端配置",
        source=app.guards_source,
        check_count=len(app.checks),
        guard_count=len(app.guards),
    )
