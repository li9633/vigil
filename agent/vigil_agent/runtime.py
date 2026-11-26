"""agent 运行时基础设施：内存状态、系统信息、日志装配与看门狗。

这一层不参与业务编排，只提供进程内共享的运行时能力，
供 `AgentApp` 与 `services/` 复用。
"""

import collections
import json
import logging
import os
import sys
import threading
import time

from .detect.base import (
    STATUS_CRITICAL,
    STATUS_OK,
    STATUS_SKIPPED,
    STATUS_UNKNOWN,
    STATUS_WARN,
)
from .logstore import (
    CompressingRotatingFileHandler,
    JsonlFormatter,
    MemoryLogHandler,
    log_event,
)
from .utils.fs import ensure_dir
from .utils.time import to_iso

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


def read_uptime(path="/proc/uptime"):
    try:
        with open(path, encoding="utf-8") as handle:
            return int(float(handle.read().split()[0]))
    except (OSError, ValueError, IndexError):
        return int(time.monotonic())


def read_first_line(path, default=""):
    try:
        with open(path, encoding="utf-8") as handle:
            value = handle.readline().strip()
            return value or default
    except OSError:
        return default


def read_fw_version(path="/etc/openwrt_release"):
    fields = {}
    try:
        with open(path, encoding="utf-8") as handle:
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


def iso_utc(epoch):
    """epoch 秒 → ISO8601 UTC 字符串，供前端时间组件直接解析。"""
    return to_iso(epoch)


def evidence_text(evidence):
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


def derive_client_state(checks):
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
    return ensure_dir(path)


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


def configure_logging(
    level_name,
    log_path,
    state,
    *,
    compress=True,
    max_bytes=2 * 1024 * 1024,
    backups=2,
):
    level = getattr(logging, str(level_name).upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    text_formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(text_formatter)
    root.addHandler(console)

    try:
        file_handler = CompressingRotatingFileHandler(
            log_path,
            max_bytes=max_bytes,
            backup_count=backups,
            compress=compress,
        )
        file_handler.setFormatter(JsonlFormatter())
        root.addHandler(file_handler)
    except OSError as exc:
        root.warning("无法写入日志文件 %s：%s", log_path, exc)

    root.addHandler(MemoryLogHandler(state))


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
                log_event(
                    "error",
                    "agent.watchdog",
                    "主循环超时，强制退出",
                    elapsed_seconds=round(elapsed, 2),
                    timeout_seconds=self.timeout,
                )
                logging.shutdown()
                os._exit(1)

    def stop(self):
        self._stop_event.set()
