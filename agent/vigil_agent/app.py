"""`AgentApp` 门面：装配配置、检测、动作、传输、调度与本地 API。

进程模型（设计文档 3.1）：
- procd 负责拉起与 respawn
- 主循环每秒 tick，按 interval 触发检测 / 心跳 / 拉配置
- 内部看门狗在主循环超过 2×检测间隔未 tick 时 os._exit(1)，交由 procd 重启
- 单实例通过 flock 保证（见 `main.py`）

业务规则与视图组装在 `services/`，HTTP 控制在 `api/`，本模块只做装配与调度。
"""

import copy
import logging
import os
import secrets
import signal
import socket
import threading
import time
import uuid

from . import __version__
from .action.executor import ActionExecutor
from .api import create_server
from .checks.model import normalize_checks
from .checks.runner import (
    STATUS_CRITICAL,
    STATUS_OK,
    STATUS_SKIPPED,
    STATUS_UNKNOWN,
    aggregate_status,
)
from .config import DEFAULT_CONFIG_PATH, save_config
from .guards import GuardStateMachine
from .guards.model import normalize_guard
from .guards.recovery import RecoveryRunner
from .logstore import log_event, set_log_context
from .probes.builtin import builtin_registry
from .probes.loader import DEFAULT_PROBES_DIR, LocalProbeStore
from .runtime import RuntimeState, Watchdog, prepare_storage
from .scheduler import IntervalScheduler
from .services import check_service, guard_service, pairing_service, uplink, views
from .spool import EventSpool
from .transport import Transport

LOGGER = logging.getLogger("vigil.agent")


class AgentApp:
    """把配置、检测、动作、传输、本地 API 串起来。"""

    def __init__(self, config, config_path=None, httpd_enabled=True):
        self.config = config
        self.config_path = config_path or DEFAULT_CONFIG_PATH
        self.httpd_enabled = httpd_enabled

        self.state = RuntimeState(__version__)
        self.storage_persistent = prepare_storage(config)

        self.spool = EventSpool(config["storage"]["spool_dir"])
        self.transport = Transport(config, save_config=self._persist_config)
        set_log_context(self.transport.client_id, self.transport.boot_id)
        self._log_throttle = {}
        self.executor = ActionExecutor(config)
        self._detect_lock = threading.Lock()
        self.probe_store = LocalProbeStore(
            str((config.get("probes") or {}).get("dir") or DEFAULT_PROBES_DIR)
        )
        self.registry = builtin_registry()
        self._reload_probes(log=False)
        self.checks = normalize_checks(list(config.get("checks") or []), self.registry)
        self.guards = [normalize_guard(item) for item in config.get("guards") or []]
        # 本地配置快照：服务端配置撤回时回退
        self._local_checks = copy.deepcopy(self.checks)
        # guards 来源：local=本地编辑；server=服务端下发（本地只读）
        self.guards_source = "local"
        self._server_guards = None
        guards_state_path = os.path.join(
            os.path.dirname(config["storage"]["state_path"]), "guards.json"
        )
        self.guard_fsm = GuardStateMachine(state_path=guards_state_path)
        self.scheduler = IntervalScheduler()

        # 调度节拍取各 Guard 间隔的最小值；每个 Guard 再按自身间隔节流。
        self.detect_interval = guard_service.detect_loop_interval(self.guards)
        self.scheduler.add("detect", self.detect_interval)
        self.scheduler.add("heartbeat", int(config["server"]["heartbeat_interval"]))
        self.scheduler.add("config", int(config["server"]["config_poll_interval"]))

        self._stop_event = threading.Event()
        self._httpd = None
        self._httpd_thread = None
        self._watchdog = None
        self._action_thread = None
        self._etag = None
        self._server_config = {}
        self._next_register_at = 0.0
        self._guard_last_run = {}

    # ---------- 生命周期 ----------

    def start(self):
        if self.httpd_enabled:
            self._start_httpd()

    def run_forever(self):
        self._install_signal_handlers()
        timeout = max(60, 2 * int(self.detect_interval))
        self._watchdog = Watchdog(self.state, timeout)
        self._watchdog.start()
        self.state.touch()
        log_event(
            "info",
            "agent.start",
            "vigil-agent 启动",
            version=self.state.agent_version,
            detect_interval=int(self.detect_interval),
            heartbeat_interval=int(self.config["server"]["heartbeat_interval"]),
        )
        try:
            while not self._stop_event.is_set():
                self.state.touch()
                due = self.scheduler.due()
                if "detect" in due:
                    self._safe(self.detect_once)
                if "heartbeat" in due:
                    self._safe(self.heartbeat_once)
                if "config" in due:
                    self._safe(self.poll_config)
                self._stop_event.wait(1.0)
        finally:
            self.shutdown()

    def run_once(self):
        """调试用：执行一轮检测与心跳后返回检测结果。"""
        self.ensure_registered()
        results = self.detect_once()
        self.heartbeat_once()
        return results

    def shutdown(self):
        self._stop_event.set()
        if self._watchdog is not None:
            self._watchdog.stop()
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
            try:
                self._httpd.server_close()
            except Exception:
                pass
            if self._httpd_thread is not None:
                self._httpd_thread.join(2)
            self._httpd = None
            self._httpd_thread = None
        LOGGER.info("vigil-agent 已退出")
        log_event("info", "agent.stop", "vigil-agent 已退出")

    def stop(self):
        self._stop_event.set()

    @staticmethod
    def _safe(func):
        try:
            func()
        except Exception:
            LOGGER.exception("调度任务执行异常：%s", getattr(func, "__name__", func))

    # ---------- 注册与心跳 ----------

    def agent_name(self):
        configured = str((self.config.get("agent") or {}).get("name") or "").strip()
        if configured:
            return configured
        return socket.gethostname() or "OpenWrt"

    def ensure_registered(self):
        return uplink.ensure_registered(self)

    def heartbeat_once(self):
        return uplink.heartbeat_once(self)

    def flush_spool(self):
        return uplink.flush_spool(self)

    def poll_config(self):
        return uplink.poll_config(self)

    # ---------- 检测与自愈 ----------

    def detect_once(self, emit_events=True, allow_actions=True):
        with self._detect_lock:
            now = time.time()
            previous = self.state.snapshot_checks()
            previous_guards = self.state.snapshot_guards()

            guard_results = self._evaluate_guards(now=now, previous=previous_guards)
            self.state.set_guards(guard_results)
            results = self.state.snapshot_checks()

            if emit_events:
                for check_id, result in results.items():
                    old_status = previous.get(check_id, {}).get("status")
                    new_status = result["status"]
                    if old_status != new_status and new_status in (
                        STATUS_OK,
                        STATUS_CRITICAL,
                        STATUS_UNKNOWN,
                    ):
                        self._record_event(
                            "check_status",
                            check_id,
                            {
                                "check_id": check_id,
                                "name": result.get("name", check_id),
                                "probe": result.get("probe", ""),
                                "status": new_status,
                                "previous": old_status or "",
                                "latency_ms": result["latency_ms"],
                                "evidence": result["evidence"],
                            },
                        )
                        LOGGER.info(
                            "检测状态变化 %s：%s -> %s",
                            check_id,
                            old_status or "未知",
                            new_status,
                        )
                self._emit_guard_events(previous_guards, guard_results)

            if allow_actions:
                self._maybe_guard_recover(guard_results)
            return results

    def _evaluate_guards(self, now=None, previous=None):
        snapshots, fresh = guard_service.evaluate_guards(
            self.guards,
            self.checks,
            self.config,
            self.registry,
            self.guard_fsm,
            self._guard_last_run,
            now=now,
            previous=previous,
        )
        if fresh:
            current = self.state.snapshot_checks()
            active = {check["id"] for check in self.checks}
            merged = {
                check_id: result
                for check_id, result in current.items()
                if check_id in active
            }
            merged.update(fresh)
            self.state.set_checks(merged)
        return snapshots

    def _emit_guard_events(self, previous, current):
        for guard_id, snapshot in current.items():
            old = previous.get(guard_id) or {}
            if not old:
                # agent 重启或新增 Guard 后的第一轮没有上一轮快照，只记录初始状态
                log_event(
                    "info",
                    "guard.state",
                    "守护项初始状态：%s" % snapshot["status"],
                    guard={
                        "id": guard_id,
                        "name": snapshot["name"],
                        "template": snapshot.get("template", ""),
                    },
                    **{
                        "from": "",
                        "to": snapshot["status"],
                        "check_status": snapshot["check_status"],
                        "failure_count": snapshot.get("failure_count", 0),
                    },
                )
                continue
            if old.get("status") == snapshot["status"]:
                continue
            self._record_event(
                "guard_status",
                guard_id,
                {
                    "guard_id": guard_id,
                    "name": snapshot["name"],
                    "status": snapshot["status"],
                    "previous": old.get("status", ""),
                    "check_status": snapshot["check_status"],
                    "failure_count": snapshot.get("failure_count", 0),
                },
            )
            status = snapshot["status"]
            if status == "GAVE_UP":
                level = "error"
            elif status in ("FAILED", "PENDING"):
                level = "warn"
            else:
                level = "info"
            log_event(
                level,
                "guard.state",
                "守护项状态变化：%s -> %s" % (old.get("status", "未知"), status),
                guard={
                    "id": guard_id,
                    "name": snapshot["name"],
                    "template": snapshot.get("template", ""),
                },
                **{
                    "from": old.get("status", ""),
                    "to": snapshot["status"],
                    "check_status": snapshot["check_status"],
                    "failure_count": snapshot.get("failure_count", 0),
                },
            )

    def _maybe_guard_recover(self, guard_results):
        safety = self.config.get("action_safety") or {}
        if not safety.get("global_enabled", True):
            return
        if self._action_thread is not None and self._action_thread.is_alive():
            return

        for guard in self.guards:
            if not guard.get("enabled", True):
                continue
            guard_id = str(guard.get("id"))
            if not self.guard_fsm.should_recover(guard_id):
                continue
            snapshot = guard_results.get(guard_id) or {}
            if snapshot.get("check_status") == STATUS_SKIPPED:
                continue
            thread = threading.Thread(
                target=self._run_guard_recovery,
                args=(guard,),
                name="vigil-recovery",
                daemon=True,
            )
            self._action_thread = thread
            thread.start()
            break

    def _run_guard_recovery(self, guard):
        guard_id = str(guard.get("id"))
        name = guard.get("name") or guard_id
        recovery_id = str(uuid.uuid4())
        started = time.monotonic()
        guard_payload = {
            "id": guard_id,
            "name": name,
            "template": guard.get("template", ""),
        }
        log_event(
            "warn",
            "guard.recover.start",
            "开始执行恢复链",
            recovery_id=recovery_id,
            guard=guard_payload,
            actions=[step.get("action") for step in guard.get("recovery") or []],
        )
        try:
            runner = RecoveryRunner(
                self.executor,
                self.guard_fsm,
                verify=lambda: self._guard_ok(guard),
                on_action=self._record_guard_action,
                on_verify=lambda ok, attempt: self._record_guard_verify(
                    guard, ok, attempt, recovery_id
                ),
                recovery_id=recovery_id,
            )
            outcome = runner.run(guard)
        except Exception:
            LOGGER.exception("执行守护项恢复链异常：%s", name)
            self.guard_fsm.abort_recovery(guard_id)
            log_event(
                "error",
                "guard.recover.error",
                "恢复链执行异常",
                recovery_id=recovery_id,
                guard=guard_payload,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return
        finally:
            self._action_thread = None

        state = outcome.get("state") or {}
        status = state.get("status", "")
        duration_ms = int((time.monotonic() - started) * 1000)
        if status == "GAVE_UP":
            event, level, message = "guard.recover.give_up", "error", "恢复链已放弃"
        elif outcome.get("recovered"):
            event, level, message = "guard.recover.result", "info", "守护项已恢复"
        elif outcome.get("skipped"):
            event, level, message = "guard.recover.result", "warn", "恢复动作被安全策略拒绝"
        else:
            event, level, message = "guard.recover.result", "warn", "恢复链未成功"
        log_event(
            level,
            event,
            message,
            recovery_id=recovery_id,
            guard=guard_payload,
            status=status,
            recovered=bool(outcome.get("recovered")),
            skipped=bool(outcome.get("skipped")),
            attempts=len(outcome.get("attempts") or []),
            duration_ms=duration_ms,
        )
        self._record_event(
            "guard_recovery",
            guard_id,
            {
                "guard_id": guard_id,
                "name": name,
                "recovered": bool(outcome.get("recovered")),
                "skipped": bool(outcome.get("skipped")),
                "status": state.get("status", ""),
                "attempts": len(outcome.get("attempts") or []),
                "recovery_id": outcome.get("recovery_id", ""),
            },
        )
        self._sync_guard_state(guard_id)

    def _record_guard_verify(self, guard, recovered, attempt, recovery_id):
        log_event(
            "info" if recovered else "warn",
            "guard.recover.verify",
            "恢复验证%s" % ("通过" if recovered else "未通过"),
            recovery_id=recovery_id,
            guard={
                "id": str(guard.get("id")),
                "name": guard.get("name") or str(guard.get("id")),
                "template": guard.get("template", ""),
            },
            attempt=attempt,
            recovered=bool(recovered),
        )

    def _sync_guard_state(self, guard_id):
        """恢复链结束后刷新快照状态，下一轮检测立即复核 Check。"""
        state = self.guard_fsm.snapshot(guard_id)
        snapshots = self.state.snapshot_guards()
        snapshot = snapshots.get(guard_id)
        if not snapshot:
            return
        for key in (
            "status",
            "failure_count",
            "success_count",
            "recovery_rounds",
            "rearm_at",
        ):
            snapshot[key] = state.get(key, snapshot.get(key))
        self.state.set_guards(snapshots)
        self._guard_last_run[guard_id] = 0

    def _record_guard_action(self, guard, result):
        guard_payload = {
            "id": str(guard.get("id")),
            "name": guard.get("name") or str(guard.get("id")),
            "template": guard.get("template", ""),
        }
        log_event(
            "info" if result.get("executed") else "warn",
            "action.result",
            "动作执行%s" % ("成功" if result.get("executed") else "被拒绝"),
            recovery_id=result.get("recovery_id"),
            attempt=result.get("attempt"),
            guard=guard_payload,
            action={
                "name": result.get("action"),
                "target": result.get("target"),
                "result": result.get("result"),
                "exit_code": result.get("exit_code"),
            },
            reason=result.get("reason"),
            duration_ms=result.get("latency_ms"),
        )
        self._record_event(
            "action_result",
            str(guard.get("id")),
            {
                "guard_id": str(guard.get("id")),
                "guard_name": guard.get("name", ""),
                "action": result.get("action"),
                "target": result.get("target"),
                "result": result.get("result"),
                "reason": result.get("reason"),
                "exit_code": result.get("exit_code"),
                "latency_ms": result.get("latency_ms"),
                "output": result.get("output"),
            },
        )

    def _guard_ok(self, guard):
        try:
            results = guard_service.run_guard_checks(
                guard, self.checks, self.registry, self.config
            )
            return (
                aggregate_status(
                    results,
                    guard.get("evaluate"),
                    guard.get("check_refs"),
                    self.checks,
                )
                == STATUS_OK
            )
        except Exception:
            LOGGER.exception("守护项复检异常")
            log_event(
                "error",
                "guard.verify.error",
                "守护项复检异常",
                guard={
                    "id": str(guard.get("id")),
                    "name": guard.get("name") or str(guard.get("id")),
                    "template": guard.get("template", ""),
                },
            )
            return False

    # ---------- 本地 API ----------

    def api_status(self):
        return views.build_status(self)

    def api_checks(self):
        return views.build_checks(self)

    def api_checks_update(self, checks):
        check_service.update_local_checks(self, checks)
        return self.api_checks()

    def api_probes(self):
        return views.build_probes(self)

    def api_probe_install(self, data):
        package = self.probe_store.install(data)
        self._reload_probes()
        self._record_event(
            "probe_installed",
            "probe",
            {"id": package.id, "version": package.version, "name": package.name},
        )
        log_event(
            "info",
            "probe.install",
            "本地 probe 包已安装",
            probe={"id": package.id, "version": package.version},
        )
        return self.api_probes()

    def api_probe_delete(self, probe_id):
        self.probe_store.remove(probe_id)
        self._reload_probes()
        self._record_event("probe_removed", "probe", {"id": probe_id})
        log_event("info", "probe.remove", "本地 probe 包已删除", probe_id=probe_id)
        return self.api_probes()

    def api_probe_test(self, probe_id, params=None, expect=None):
        return views.run_probe_test(self, probe_id, params, expect)

    def api_check_test(self, check_id):
        return views.run_check_test(self, check_id)

    def _reload_probes(self, log=True):
        registry = builtin_registry()
        errors = self.probe_store.load_into(registry)
        with self._detect_lock:
            self.registry = registry
        for message in errors:
            LOGGER.warning("本地 probe 包不可用：%s", message)
            if log:
                log_event("warn", "probe.invalid", "本地 probe 包不可用", detail=message)
        return registry

    def api_guards(self):
        return guard_service.build_guards(self)

    def api_guards_update(self, guards):
        guard_service.update_local_guards(self, guards)
        return self.api_guards()

    def api_settings(self):
        if not self.transport.is_registered:
            pairing_service.poll(self)
        return views.build_settings(self)

    def api_pairing_start(self, base_url):
        pairing_service.start(self, base_url)
        return views.build_settings(self)

    def api_pairing_poll(self):
        pairing_service.poll(self, force=True)
        return views.build_settings(self)

    def api_pairing_cancel(self):
        pairing_service.cancel(self)
        return views.build_settings(self)

    def api_unregister(self):
        return uplink.unregister(self)

    def api_settings_update(self, payload):
        parsed = views.parse_settings_update(payload)
        changed = []
        if parsed["base_url"] is not None:
            self.transport.update_server(parsed["base_url"])
            self._etag = None
            self._server_config = {}
            changed.append("server.base_url")
        if parsed["heartbeat"] is not None:
            self.config.setdefault("server", {})["heartbeat_interval"] = parsed["heartbeat"]
            self.scheduler.update_interval("heartbeat", parsed["heartbeat"])
            changed.append("server.heartbeat_interval")
        if parsed["safety_updates"]:
            safety = self.config.setdefault("action_safety", {})
            safety.update(parsed["safety_updates"])
            self.executor.apply_safety(parsed["safety_updates"])
            changed.append("action_safety")
        if parsed["maintenance_minutes"] is not None:
            self.executor.set_maintenance(parsed["maintenance_minutes"] * 60)
            changed.append("maintenance")

        if changed:
            self._persist_config(self.config)
            self._record_event("settings_updated", "config", {"changed": changed})
            log_event("info", "settings.update", "设置已更新", changed=changed)
        return self.api_settings()

    def api_events(self, limit=50):
        return views.list_events(self.state, self.spool, limit)

    def api_logs(
        self,
        limit=200,
        level=None,
        event=None,
        guard_id=None,
        recovery_id=None,
        since=None,
    ):
        return views.query_logs(
            self.config,
            self.state,
            limit=limit,
            level=level,
            event=event,
            guard_id=guard_id,
            recovery_id=recovery_id,
            since=since,
        )

    def api_logs_export(
        self,
        level=None,
        event=None,
        guard_id=None,
        recovery_id=None,
        since=None,
    ):
        return views.export_logs(
            self.config,
            self.state,
            level=level,
            event=event,
            guard_id=guard_id,
            recovery_id=recovery_id,
            since=since,
        )

    def api_action(self, name, target=None):
        result = self.executor.execute(name, target, manual=True)
        self._record_event(
            "manual_action",
            "action",
            {
                "action": name,
                "target": target,
                "allowed": bool(result.get("allowed")),
                "result": result.get("result"),
                "reason": result.get("reason"),
                "exit_code": result.get("exit_code"),
            },
        )
        log_event(
            "info" if result.get("executed") else "warn",
            "action.result",
            "手动动作执行%s" % ("成功" if result.get("executed") else "被拒绝"),
            manual=True,
            action={"name": name, "target": target, "result": result.get("result")},
            reason=result.get("reason"),
            duration_ms=result.get("latency_ms"),
        )
        return result

    # ---------- 内部工具 ----------

    def _record_event(self, event_type, domain, payload):
        event = {"type": event_type, "domain": domain, "payload": payload}
        try:
            stored = self.spool.append(event)
        except Exception:
            LOGGER.exception("写入本地 spool 失败")
            stored = {
                "type": event_type,
                "domain": domain,
                "payload": payload,
                "seq": 0,
                "event_id": "",
                "ts": int(time.time()),
            }
        self.state.add_event(stored)

    def _log_throttled(self, key, level, event, msg, interval=60, **fields):
        """同一类失败按间隔节流，并记录被抑制的次数。"""
        now = time.monotonic()
        last, suppressed = self._log_throttle.get(key, (0.0, 0))
        if last and now - last < interval:
            self._log_throttle[key] = (last, suppressed + 1)
            return
        self._log_throttle[key] = (now, 0)
        if suppressed:
            fields["suppressed"] = suppressed
        log_event(level, event, msg, **fields)

    def _persist_config(self, config):
        if not self.config_path:
            return
        save_config(config, self.config_path)

    def _resolve_token(self):
        token = str((self.config.get("httpd") or {}).get("token") or "")
        if token:
            return token
        token_path = os.path.join(
            os.path.dirname(self.config_path or DEFAULT_CONFIG_PATH), "admin_token"
        )
        try:
            with open(token_path, encoding="utf-8") as handle:
                existing = handle.read().strip()
            if existing:
                return existing
        except OSError:
            pass

        generated = _new_token()
        try:
            directory = os.path.dirname(token_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(token_path, "w", encoding="utf-8") as handle:
                handle.write(generated)
            try:
                os.chmod(token_path, 0o600)
            except OSError:
                pass
            LOGGER.warning("已生成管理员令牌并写入 %s", token_path)
        except OSError as exc:
            LOGGER.error("无法写入管理员令牌文件 %s：%s", token_path, exc)
        return generated

    def _start_httpd(self):
        httpd_cfg = self.config.get("httpd") or {}
        token = self._resolve_token()
        try:
            server = create_server(
                httpd_cfg.get("bind", "0.0.0.0"),
                httpd_cfg.get("port", 8787),
                token,
                self.config["storage"].get("www_root", "/usr/share/vigil/www"),
                status_provider=self.api_status,
                checks_provider=self.api_checks,
                checks_handler=self.api_checks_update,
                events_provider=self.api_events,
                logs_provider=self.api_logs,
                logs_export_provider=self.api_logs_export,
                action_handler=self.api_action,
                session_ttl=int(httpd_cfg.get("session_ttl", 43200)),
                login_max_failures=int(httpd_cfg.get("login_max_failures", 5)),
                login_window=int(httpd_cfg.get("login_window", 60)),
                guards_provider=self.api_guards,
                guards_handler=self.api_guards_update,
                settings_provider=self.api_settings,
                settings_handler=self.api_settings_update,
                pairing_start_handler=self.api_pairing_start,
                pairing_poll_handler=self.api_pairing_poll,
                pairing_cancel_handler=self.api_pairing_cancel,
                unregister_handler=self.api_unregister,
                probes_provider=self.api_probes,
                probes_install_handler=self.api_probe_install,
                probes_delete_handler=self.api_probe_delete,
                probes_test_handler=self.api_probe_test,
                checks_test_handler=self.api_check_test,
            )
        except OSError as exc:
            LOGGER.error("本地 HTTP 服务启动失败：%s", exc)
            return

        self._httpd = server
        self._httpd_thread = threading.Thread(
            target=server.serve_forever, name="vigil-httpd", daemon=True
        )
        self._httpd_thread.start()
        LOGGER.info(
            "本地 UI/API 监听 %s:%s",
            httpd_cfg.get("bind", "0.0.0.0"),
            httpd_cfg.get("port", 8787),
        )

    def _install_signal_handlers(self):
        def handler(signum, _frame):
            LOGGER.info("收到信号 %s，准备退出", signum)
            self._stop_event.set()

        for name in ("SIGTERM", "SIGINT"):
            signum = getattr(signal, name, None)
            if signum is None:
                continue
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError):
                pass


def _new_token():
    return secrets.token_urlsafe(24)
