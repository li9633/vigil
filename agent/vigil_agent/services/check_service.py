"""本地检测项配置：整表替换、校验与持久化。"""

from __future__ import annotations

import copy

from ..checks.model import normalize_checks, validate_checks
from ..config import save_config
from ..guards.model import normalize_guard, validate_guards
from ..logstore import log_event
from . import guard_service


def _local_guards(app):
    return [normalize_guard(item) for item in app.config.get("guards") or []]


def update_local_checks(app, checks):
    """PUT /api/checks：整表替换本地 Check 配置并立即生效。

    Guard 与 Check 均归客户端管理；保存前同时校验现有 Guard 的 check_refs。
    """
    if not isinstance(checks, list):
        raise ValueError("checks 必须是数组")

    candidate = normalize_checks(checks, app.registry)
    errors = []
    validate_checks(candidate, app.registry, errors)
    guards = app.guards if app.guards_source == "local" else _local_guards(app)
    validate_guards({"guards": guards}, errors, checks=candidate)
    if errors:
        raise ValueError("；".join(errors))

    with app._detect_lock:
        app.config["checks"] = copy.deepcopy(candidate)
        app.checks = copy.deepcopy(candidate)
        app._local_checks = copy.deepcopy(candidate)
        if app.guards_source != "local":
            # 客户端接管配置，回退本地 Guard
            app.guards = copy.deepcopy(guards)
            app.guards_source = "local"
            app._server_guards = None
        app._guard_last_run.clear()
        app.detect_interval = guard_service.detect_loop_interval(app.guards)
        app.scheduler.update_interval("detect", app.detect_interval)

    save_config(app.config, app.config_path)
    app._record_event(
        "config_updated",
        "checks",
        {"source": "local", "count": len(candidate), "checks": [item["id"] for item in candidate]},
    )
    log_event(
        "info",
        "check_config.update",
        "本地检测项配置已更新并持久化",
        count=len(candidate),
        source="local",
    )
    return candidate
