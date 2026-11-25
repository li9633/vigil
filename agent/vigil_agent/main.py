"""agent 入口：调度、看门狗、优雅退出。

进程模型（设计文档 3.1）：
- procd 负责拉起与 respawn
- 主循环每秒 tick，按 interval 触发检测 / 心跳 / 拉配置
- 内部看门狗在主循环超过 2×检测间隔未 tick 时 os._exit(1)，交由 procd 重启
- 单实例通过 flock 保证
"""

import argparse
import collections
import copy
import json
import logging
import logging.handlers
import os
import signal
import socket
import sys
import threading
import time
from datetime import datetime, timezone

from . import __version__
from .action.executor import ActionExecutor
from .config import DEFAULT_CONFIG_PATH, ConfigError, load_config, save_config
from .detect.base import (
    CommandRunner,
    DetectContext,
    DetectionEngine,
    STATUS_CRITICAL,
    STATUS_OK,
    STATUS_SKIPPED,
    STATUS_UNKNOWN,
    STATUS_WARN,
)
from .httpd import create_server
from .guards import GuardStateMachine, aggregate_status, run_checks, validate_guards
from .guards.recovery import RecoveryRunner
from .lockfile import FileLock
from .scheduler import IntervalScheduler
from .spool import EventSpool
from .transport import Transport

LOGGER = logging.getLogger("vigil.agent")


class RuntimeState:
    """内存运行状态。进程重启即清零（动作冷却状态由 executor 持久化）。"""

    def __init__(self, agent_version, max_events=200, max_logs=500):
        self.lock = threading.RLock()
        self.agent_version = agent_version
        self.checks = {}
        self.guards = {}
        self.events = collections.deque(maxlen=max_events)
        self.logs = collections.deque(maxlen=max_logs)
        self.started_at = time.time()
        self.last_heartbeat = None
        self.last_check_at = None
        self.last_tick = time.monotonic()

    def touch(self):
        self.last_tick = time.monotonic()

    def set_checks(self, results):
        with self.lock:
            self.checks = dict(results)
            self.last_check_at = time.time()

    def snapshot_checks(self):
        with self.lock:
            return dict(self.checks)

    def set_guards(self, results):
        with self.lock:
            self.guards = dict(results)

    def snapshot_guards(self):
        with self.lock:
            return dict(self.guards)

    def add_event(self, event):
        with self.lock:
            self.events.append(dict(event))

    def add_log(self, item):
        with self.lock:
            self.logs.append(dict(item))

    def events_snapshot(self, limit):
        with self.lock:
            items = list(self.events)
        limit = max(0, int(limit))
        return items[-limit:] if limit else []

    def logs_snapshot(self, limit):
        with self.lock:
            items = list(self.logs)
        limit = max(0, int(limit))
        return items[-limit:] if limit else []


class MemoryLogHandler(logging.Handler):
    """把日志同步到内存环形缓冲，供本地 API 查询。"""

    def __init__(self, state):
        super().__init__()
        self.state = state

    def emit(self, record):
        try:
            self.state.add_log(
                {
                    "ts": int(record.created),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                }
            )
        except Exception:  # 日志链路本身不能抛异常
            pass


def read_uptime(path="/proc/uptime"):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return int(float(handle.read().split()[0]))
    except (OSError, ValueError, IndexError):
        return int(time.monotonic())


def read_first_line(path, default=""):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = handle.readline().strip()
            return value or default
    except OSError:
        return default


def read_fw_version(path="/etc/openwrt_release"):
    fields = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                fields[key.strip()] = value.strip().strip("'\"")
    except OSError:
        return ""
    description = fields.get("DISTRIB_DESCRIPTION")
    if description:
        return description
    parts = [fields.get("DISTRIB_ID"), fields.get("DISTRIB_RELEASE")]
    return " ".join([part for part in parts if part])


def _iso_utc(epoch):
    """epoch 秒 → ISO8601 UTC 字符串，供前端时间组件直接解析。"""
    if epoch is None:
        return None
    moment = datetime.fromtimestamp(float(epoch), tz=timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _evidence_text(evidence):
    """把检测证据对象压成一行可读文本，前端表格按字符串直接展示。"""
    if not evidence:
        return ""
    if isinstance(evidence, str):
        return evidence
    if not isinstance(evidence, dict):
        return str(evidence)
    parts = []
    for key in (
        "reason",
        "error",
        "device",
        "interface",
        "up",
        "uptime",
        "egress_ip",
        "consecutive_failures",
    ):
        value = evidence.get(key)
        if value is None or value == "":
            continue
        parts.append("%s=%s" % (key, value))
    if parts:
        return ", ".join(parts)
    return json.dumps(evidence, ensure_ascii=False)[:512]


def _derive_client_state(checks):
    """由各层检测结果派生一个概览状态（与 ClientState 语义对齐）。"""
    statuses = [item.get("status") for item in checks.values()]
    if not statuses:
        return "UNKNOWN"
    if any(status == STATUS_CRITICAL for status in statuses):
        return "DOWN"
    if any(status in (STATUS_WARN, STATUS_UNKNOWN) for status in statuses):
        return "SUSPECT"
    if all(status in (STATUS_OK, STATUS_SKIPPED) for status in statuses):
        return "HEALTHY"
    return "UNKNOWN"


def _ensure_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except OSError:
        return False


def prepare_storage(config):
    """创建存储目录；/overlay 不可用时回退到 /tmp（返回是否持久化）。"""
    storage = config.get("storage") or {}
    fallbacks = {
        "state_path": "/tmp/vigil/state.json",
        "log_path": "/tmp/vigil/vigil.log",
        "lock_path": "/tmp/vigil/vigil.lock",
        "spool_dir": "/tmp/vigil/spool",
        "run_dir": "/tmp/vigil/run",
    }
    persistent = True
    for key, fallback in fallbacks.items():
        current = storage.get(key)
        if not isinstance(current, str) or not current:
            continue
        directory = current if key.endswith("_dir") else os.path.dirname(current)
        if directory and not _ensure_dir(directory):
            storage[key] = fallback
            persistent = False
            directory = os.path.dirname(fallback) if not fallback.endswith("/") else fallback
            _ensure_dir(directory)
    return persistent


def configure_logging(level_name, log_path, state):
    level = getattr(logging, str(level_name).upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    root.addHandler(console)

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=1024 * 1024, backupCount=2, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as exc:
        root.warning("无法写入日志文件 %s：%s", log_path, exc)

    memory = MemoryLogHandler(state)
    memory.setFormatter(formatter)
    root.addHandler(memory)


class Watchdog(threading.Thread):
    """主循环看门狗：超时直接 os._exit(1)，交给 procd 重启。"""

    def __init__(self, state, timeout):
        super().__init__(name="vigil-watchdog", daemon=True)
        self.state = state
        self.timeout = float(timeout)
        self._stop_event = threading.Event()

    def run(self):
        while not self._stop_event.wait(5):
            elapsed = time.monotonic() - self.state.last_tick
            if elapsed > self.timeout:
                LOGGER.critical(
                    "主循环 %.1fs 未 tick，超过看门狗阈值 %.1fs，强制退出",
                    elapsed,
                    self.timeout,
                )
                logging.shutdown()
                os._exit(1)

    def stop(self):
        self._stop_event.set()


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
        self.executor = ActionExecutor(config)
        self.engine = DetectionEngine()
        self.guards = list(config.get("guards") or [])
        # guards 来源：local=本地编辑；server=服务端下发（本地只读）
        self.guards_source = "local"
        self._server_guards = None
        guards_state_path = os.path.join(
            os.path.dirname(config["storage"]["state_path"]), "guards.json"
        )
        self.guard_fsm = GuardStateMachine(state_path=guards_state_path)
        self.scheduler = IntervalScheduler()

        # 调度节拍取「旧检测间隔」与「各 Guard 间隔」的最小值；每一项再按自身间隔节流。
        self.scheduler.add("detect", self._detect_loop_interval(self.guards))
        self.scheduler.add("heartbeat", int(config["server"]["heartbeat_interval"]))
        self.scheduler.add("config", int(config["server"]["config_poll_interval"]))

        self._stop_event = threading.Event()
        self._detect_lock = threading.Lock()
        self._httpd = None
        self._httpd_thread = None
        self._watchdog = None
        self._action_thread = None
        self._etag = None
        self._server_config = {}
        self._next_register_at = 0.0
        self._last_engine_at = 0.0
        self._guard_last_run = {}

    # ---------- 生命周期 ----------

    def start(self):
        if self.httpd_enabled:
            self._start_httpd()

    def run_forever(self):
        self._install_signal_handlers()
        timeout = max(60, 2 * int(self.config["detect"]["interval"]))
        self._watchdog = Watchdog(self.state, timeout)
        self._watchdog.start()
        self.state.touch()
        LOGGER.info(
            "vigil-agent %s 启动（检测间隔 %ss，心跳 %ss）",
            __version__,
            self.config["detect"]["interval"],
            self.config["server"]["heartbeat_interval"],
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
        if self.transport.is_registered:
            return True
        now = time.time()
        if now < self._next_register_at:
            return False
        server = self.config.get("server") or {}
        if not server.get("base_url") or not server.get("bootstrap_token"):
            self._next_register_at = now + 300
            return False

        result = self.transport.register(
            self.agent_name(),
            model=read_first_line("/tmp/sysinfo/model"),
            fw_version=read_fw_version(),
        )
        if result.get("ok"):
            self._next_register_at = 0.0
            self.scheduler.update_interval(
                "heartbeat", int(self.config["server"]["heartbeat_interval"])
            )
            self._record_event(
                "agent_registered",
                "agent",
                {"client_id": self.transport.client_id},
            )
            LOGGER.info("注册成功，client_id=%s", self.transport.client_id)
            return True

        self._next_register_at = now + 300
        LOGGER.warning("注册失败：%s（5 分钟后重试）", result.get("error"))
        return False

    def heartbeat_once(self):
        self.ensure_registered()
        if not self.transport.is_registered:
            return
        checks = self.state.snapshot_checks()
        modules = self._modules_snapshot(checks)
        stats = self.spool.stats()
        seq = self.spool.next_seq(persist=False)
        result = self.transport.heartbeat(
            seq,
            read_uptime(),
            modules=modules,
            guards=self._guards_payload(),
            spool={"pending": stats["pending"], "dropped": stats["dropped"]},
            wan_ip=self._wan_ip(checks),
        )
        if result.get("ok"):
            self.state.last_heartbeat = time.time()
            self.flush_spool()
        else:
            LOGGER.warning("心跳失败：%s", result.get("error"))

    def flush_spool(self):
        for _ in range(3):
            batch = self.spool.pending(50)
            if not batch:
                return
            result = self.transport.events(batch)
            if not result.get("ok"):
                LOGGER.warning("事件补传失败：%s", result.get("error"))
                return
            body = result.get("body") or {}
            acked = body.get("acked") or body.get("acked_seqs")
            if not isinstance(acked, list):
                acked = [event.get("seq") for event in batch]
            self.spool.ack([item for item in acked if isinstance(item, int)])

    def poll_config(self):
        result = self.transport.fetch_config(self._etag)
        if not result.get("ok"):
            LOGGER.warning("拉取服务端配置失败：%s", result.get("error"))
            return
        if result.get("status") == 304:
            return
        if result.get("etag"):
            self._etag = result["etag"]
        body = result.get("body") or {}
        self._server_config = body if isinstance(body, dict) else {}
        self._apply_server_guards(self._server_config)
        self._record_event(
            "config_received",
            "agent",
            {"etag": self._etag or "", "keys": sorted(self._server_config.keys())[:20]},
        )
        LOGGER.info(
            "已接收服务端配置（%d 个键，etag=%s）",
            len(self._server_config),
            self._etag or "-",
        )

    def _apply_server_guards(self, server_config):
        """应用服务端下发的 guards（热更新）。

        - 含 guards 列表且校验通过：切换到 server 来源，本地不可编辑
        - 未含 guards 键：回退到本地配置
        - 校验失败：只记日志并保持当前 guards 不变
        """
        raw = server_config.get("guards") if isinstance(server_config, dict) else None
        if isinstance(raw, list):
            errors = []
            validate_guards({"guards": raw}, errors)
            if errors:
                LOGGER.warning(
                    "服务端下发的 guards 非法，保持当前配置：%s", "；".join(errors)
                )
                return
            guards = copy.deepcopy(raw)
            source = "server"
        else:
            if self.guards_source == "local" and self._server_guards is None:
                # 已经是本地配置，无需重复替换与重置节流
                return
            guards = copy.deepcopy(self.config.get("guards") or [])
            source = "local"

        # guards 替换与 detect_once 互斥，避免检测过程中被换掉
        with self._detect_lock:
            self.guards = copy.deepcopy(guards)
            self.guards_source = source
            self._server_guards = copy.deepcopy(raw) if source == "server" else None
            self._guard_last_run.clear()
            self.scheduler.update_interval(
                "detect", self._detect_loop_interval(self.guards)
            )
        LOGGER.info(
            "已应用%s Guard 配置（%d 项）",
            "服务端下发" if source == "server" else "本地",
            len(self.guards),
        )

    # ---------- 检测与自愈 ----------

    def _detect_loop_interval(self, guards):
        """计算 detect 调度节拍：旧 detect.interval 与启用 Guard 间隔的最小值。"""
        try:
            intervals = [int(self.config["detect"]["interval"])]
        except (KeyError, TypeError, ValueError):
            intervals = [30]
        for guard in guards or []:
            if not guard.get("enabled", True):
                continue
            try:
                value = int((guard.get("policy") or {}).get("interval") or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                intervals.append(value)
        return min(intervals)

    def detect_once(self, emit_events=True, allow_actions=True):
        with self._detect_lock:
            now = time.time()
            previous = self.state.snapshot_checks()
            engine_interval = int(self.config["detect"]["interval"])
            if now - self._last_engine_at >= engine_interval or not previous:
                ctx = DetectContext(self.config, runner=CommandRunner())
                results = self.engine.run(ctx)
                self.state.set_checks(results)
                self._last_engine_at = now
            else:
                # 调度节拍可能小于旧检测间隔（被某个 Guard 拉快），此处按原间隔节流
                results = previous

            previous_guards = self.state.snapshot_guards()
            guard_results = self._evaluate_guards(now=now, previous=previous_guards)
            self.state.set_guards(guard_results)

            if emit_events:
                for layer, result in results.items():
                    old_status = previous.get(layer, {}).get("status")
                    new_status = result["status"]
                    if old_status != new_status and new_status in (STATUS_OK, STATUS_CRITICAL):
                        self._record_event(
                            "module_status",
                            layer,
                            {
                                "layer": layer,
                                "status": new_status,
                                "previous": old_status or "",
                                "latency_ms": result["latency_ms"],
                                "evidence": result["evidence"],
                            },
                        )
                        LOGGER.info(
                            "检测状态变化 %s：%s -> %s",
                            layer,
                            old_status or "未知",
                            new_status,
                        )
                self._emit_guard_events(previous_guards, guard_results)

            if allow_actions:
                self._maybe_guard_recover(guard_results)
            return results

    def _evaluate_guards(self, now=None, previous=None):
        """执行到期的 Guard Check，并推进各自的状态机。

        每个 Guard 按 policy.interval 节流；未到期时沿用上一轮快照，
        既保证心跳中的状态连续，也避免重复探测。
        """
        now = float(now if now is not None else time.time())
        previous = previous or {}
        runner = CommandRunner()
        snapshots = {}
        for guard in self.guards:
            guard_id = str(guard.get("id"))
            name = guard.get("name") or guard_id
            if not guard.get("enabled", True):
                self._guard_last_run.pop(guard_id, None)
                snapshots[guard_id] = {
                    "id": guard_id,
                    "name": name,
                    "template": guard.get("template", ""),
                    "status": "DISABLED",
                    "check_status": STATUS_SKIPPED,
                    "checks": {},
                }
                continue

            interval = int((guard.get("policy") or {}).get("interval") or 30)
            last_run = self._guard_last_run.get(guard_id)
            cached = previous.get(guard_id)
            if last_run is not None and cached is not None and now - last_run < interval:
                snapshots[guard_id] = cached
                continue

            checks = run_checks(guard, self.config, runner=runner)
            check_status = aggregate_status(checks, guard.get("evaluate"))
            fsm_state = self.guard_fsm.process(guard, check_status, now=now)
            snapshots[guard_id] = {
                "id": guard_id,
                "name": name,
                "template": guard.get("template", ""),
                "status": fsm_state["status"],
                "check_status": check_status,
                "failure_count": fsm_state["failure_count"],
                "success_count": fsm_state["success_count"],
                "recovery_rounds": fsm_state["recovery_rounds"],
                "rearm_at": fsm_state["rearm_at"],
                "checks": checks,
            }
            self._guard_last_run[guard_id] = now
        return snapshots

    def _emit_guard_events(self, previous, current):
        for guard_id, snapshot in current.items():
            old = previous.get(guard_id) or {}
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
            LOGGER.info(
                "守护项 %s 状态变化：%s -> %s",
                snapshot["name"],
                old.get("status", "未知"),
                snapshot["status"],
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
            LOGGER.info("触发守护项恢复链：%s", guard.get("name") or guard_id)
            thread.start()
            break

    def _run_guard_recovery(self, guard):
        guard_id = str(guard.get("id"))
        name = guard.get("name") or guard_id
        try:
            runner = RecoveryRunner(
                self.executor,
                self.guard_fsm,
                verify=lambda: self._guard_ok(guard),
                on_action=self._record_guard_action,
            )
            outcome = runner.run(guard)
        except Exception:
            LOGGER.exception("执行守护项恢复链异常：%s", name)
            self.guard_fsm.abort_recovery(guard_id)
            return
        finally:
            self._action_thread = None

        state = outcome.get("state") or {}
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
            },
        )
        LOGGER.info(
            "守护项 %s 恢复链结束：恢复=%s 状态=%s 动作数=%d",
            name,
            outcome.get("recovered"),
            state.get("status", ""),
            len(outcome.get("attempts") or []),
        )
        self._sync_guard_state(guard_id)

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
            checks = run_checks(guard, self.config, runner=CommandRunner())
            return aggregate_status(checks, guard.get("evaluate")) == STATUS_OK
        except Exception:
            LOGGER.exception("守护项复检异常")
            return False

    def _guards_payload(self):
        """心跳中的 Guard 快照，按 guard_id 索引。"""
        payload = {}
        for guard_id, snapshot in self.state.snapshot_guards().items():
            payload[guard_id] = {
                "id": guard_id,
                "name": snapshot.get("name", ""),
                "template": snapshot.get("template", ""),
                "status": snapshot.get("status", ""),
                "check_status": snapshot.get("check_status", ""),
                "failure_count": snapshot.get("failure_count", 0),
                "checks": snapshot.get("checks", {}),
            }
        return payload

    # ---------- 本地 API ----------

    def api_status(self):
        checks = self.state.snapshot_checks()
        order = self.engine.registry.ordered()
        wan = self._wan_info(checks)
        action_stats = self.executor.stats()
        now = time.time()
        modules = []
        for layer in order:
            result = checks.get(layer)
            if not result:
                continue
            module = {
                "id": layer,
                "name": layer,
                "layer": layer,
                "status": result.get("status"),
                "latency_ms": result.get("latency_ms"),
            }
            uptime = (result.get("evidence") or {}).get("uptime")
            if isinstance(uptime, int) and not isinstance(uptime, bool):
                module["uptime"] = uptime
            modules.append(module)

        last_heartbeat = self.state.last_heartbeat
        return {
            # 兼容字段（原有本地 API 消费者）
            "agent_version": __version__,
            "registered": bool(self.transport.is_registered),
            "uptime": read_uptime(),
            "started_at": int(self.state.started_at),
            "storage_persistent": self.storage_persistent,
            "server": {
                "base_url": self.config["server"].get("base_url", ""),
                "last_heartbeat": last_heartbeat,
                "config_etag": self._etag or "",
            },
            "wan": wan,
            "checks": [checks[layer] for layer in order if layer in checks],
            "spool": self.spool.stats(),
            "actions": action_stats,
            # 前端 ClientStatusSnapshot 契约字段
            "state": _derive_client_state(checks),
            "boot_id": self.transport.boot_id,
            "wan_ip": wan.get("ip") or None,
            "lan_ip": None,
            "carrier": "pppoe" if wan.get("pppoe_up") else (wan.get("device") or None),
            "pppoe_uptime": wan.get("pppoe_uptime"),
            "server_connected": bool(last_heartbeat),
            "last_heartbeat_at": _iso_utc(last_heartbeat),
            "last_beacon_at": None,
            "modules": modules,
            "guards": list(self.state.snapshot_guards().values()),
            "guards_source": self.guards_source,
            "guards_editable": self.guards_source == "local",
            "latest_checks": self._check_items(checks),
            "maintenance": bool(float(action_stats.get("maintenance_until") or 0) > now),
            "generated_at": _iso_utc(now),
        }

    def _check_items(self, checks=None):
        """把检测结果整理成前端 CheckItem 列表。"""
        checks = checks if checks is not None else self.state.snapshot_checks()
        updated_at = _iso_utc(self.state.last_check_at)
        items = []
        for layer in self.engine.registry.ordered():
            result = checks.get(layer)
            if not result:
                continue
            items.append(
                {
                    "id": layer,
                    "name": layer,
                    "layer": layer,
                    "status": result.get("status"),
                    "latency_ms": result.get("latency_ms"),
                    "interval": int(self.config["detect"]["interval"]),
                    "enabled": True,
                    "evidence": _evidence_text(result.get("evidence")),
                    "updated_at": updated_at,
                }
            )
        return items

    def api_checks(self):
        return self._check_items()

    def api_guards(self):
        """GET /api/guards：返回当前生效的 Guard 配置及来源。"""
        return {
            "items": copy.deepcopy(self.guards),
            "source": self.guards_source,
            "editable": self.guards_source == "local",
        }

    def api_guards_update(self, guards):
        """PUT /api/guards：本地整表替换 Guard 配置并立即生效。"""
        if self.guards_source == "server":
            raise ValueError("managed_by_server")
        if not isinstance(guards, list):
            raise ValueError("guards 必须是数组")

        candidate = copy.deepcopy(guards)
        errors = []
        validate_guards({"guards": candidate}, errors)
        if errors:
            raise ValueError("；".join(errors))

        with self._detect_lock:
            self.config["guards"] = copy.deepcopy(candidate)
            self.guards = copy.deepcopy(candidate)
            self.guards_source = "local"
            self._server_guards = None
            self._guard_last_run.clear()
            self.scheduler.update_interval(
                "detect", self._detect_loop_interval(self.guards)
            )

        save_config(self.config, self.config_path)
        self._record_event(
            "config_updated",
            "guards",
            {
                "source": "local",
                "count": len(candidate),
                "guards": [guard.get("id") for guard in candidate],
            },
        )
        LOGGER.info("本地 Guard 配置已更新并持久化（%d 项）", len(candidate))
        return self.api_guards()

    def api_events(self, limit=50):
        items = self.state.events_snapshot(limit)
        try:
            pending = {
                entry.get("seq") for entry in self.spool.pending(limit=100000)
            }
        except Exception:  # spool 读取失败时不影响事件展示
            pending = set()
        return [self._serialize_event_item(item, pending) for item in items]

    @staticmethod
    def _serialize_event_item(item, pending):
        """把 spool 中的原始记录整理成前端 ClientEvent 契约结构。"""
        payload = item.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {"value": payload}
        level = payload.get("status") or payload.get("level")
        if level not in (STATUS_OK, STATUS_WARN, STATUS_CRITICAL, STATUS_UNKNOWN):
            result = str(payload.get("result") or "").lower()
            if payload.get("allowed") is False:
                level = STATUS_WARN
            elif result in ("success", "ok", "applied"):
                level = STATUS_OK
            elif result in ("failed", "error", "timeout"):
                level = STATUS_CRITICAL
            else:
                level = STATUS_UNKNOWN
        seq = item.get("seq")
        return {
            "event_id": item.get("event_id") or "",
            "ts": _iso_utc(item.get("ts")),
            "type": item.get("type"),
            "module": item.get("domain") or None,
            "level": level,
            "message": payload.get("message") or payload.get("reason") or None,
            "uploaded": seq not in pending,
            "payload": payload,
        }

    def api_logs(self, limit=50):
        return self.state.logs_snapshot(limit)

    def api_action(self, name, target=None):
        result = self.executor.execute(name, target)
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
        LOGGER.info(
            "手动动作 %s 目标=%s 结果=%s 原因=%s",
            name,
            target or "-",
            result.get("result"),
            result.get("reason") or "-",
        )
        return result

    # ---------- 内部工具 ----------

    def _modules_snapshot(self, checks):
        modules = {}
        for layer in self.engine.registry.ordered():
            result = checks.get(layer)
            if not result:
                continue
            item = {
                "status": result.get("status"),
                "latency_ms": int(result.get("latency_ms") or 0),
            }
            if layer == "net.pppoe":
                uptime = (result.get("evidence") or {}).get("uptime")
                if isinstance(uptime, int):
                    item["uptime"] = uptime
            modules[layer] = item
        return modules

    def _wan_ip(self, checks):
        evidence = (checks.get("net.route") or {}).get("evidence") or {}
        return evidence.get("egress_ip") or ""

    def _wan_info(self, checks):
        carrier = (checks.get("net.carrier") or {}).get("evidence") or {}
        pppoe = (checks.get("net.pppoe") or {}).get("evidence") or {}
        return {
            "device": carrier.get("device") or "",
            "interface": str(self.config["detect"].get("wan_interface") or "wan"),
            "ip": self._wan_ip(checks),
            "carrier": carrier.get("carrier", ""),
            "pppoe_up": bool(pppoe.get("up")),
            "pppoe_uptime": int(pppoe.get("uptime") or 0),
        }

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
            with open(token_path, "r", encoding="utf-8") as handle:
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
                events_provider=self.api_events,
                logs_provider=self.api_logs,
                action_handler=self.api_action,
                session_ttl=int(httpd_cfg.get("session_ttl", 43200)),
                login_max_failures=int(httpd_cfg.get("login_max_failures", 5)),
                login_window=int(httpd_cfg.get("login_window", 60)),
                guards_provider=self.api_guards,
                guards_handler=self.api_guards_update,
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
    import secrets

    return secrets.token_urlsafe(24)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Vigil OpenWrt 客户端 agent")
    parser.add_argument("--config", default=None, help="配置文件路径")
    parser.add_argument("--once", action="store_true", help="执行一轮检测与心跳后退出")
    parser.add_argument("--no-httpd", action="store_true", help="不启动本地 HTTP 服务")
    parser.add_argument("--log-level", default="INFO", help="日志级别")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    config_path = args.config or os.environ.get("VIGIL_CONFIG") or DEFAULT_CONFIG_PATH
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        sys.stderr.write("配置错误：%s\n" % exc)
        return 2

    lock = FileLock(config["storage"]["lock_path"])
    if not lock.acquire():
        sys.stderr.write("已有 vigil-agent 实例在运行（%s）\n" % config["storage"]["lock_path"])
        return 3

    try:
        app = AgentApp(
            config,
            config_path=config_path,
            httpd_enabled=not args.no_httpd and not args.once,
        )
        configure_logging(args.log_level, config["storage"]["log_path"], app.state)
        if not app.storage_persistent:
            LOGGER.warning("持久化存储不可用，已回退到 /tmp，spool 与状态不会跨重启保留")

        if args.once:
            results = app.run_once()
            summary = {layer: result["status"] for layer, result in results.items()}
            sys.stdout.write(json.dumps(summary, ensure_ascii=False) + "\n")
            return 0

        app.start()
        app.run_forever()
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
