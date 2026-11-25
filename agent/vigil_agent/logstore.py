"""客户端日志核心：JSONL 格式化、轮转压缩、内存缓冲与查询。

日志行遵循设计文档的 v1 信封：

    {"v":1,"ts":"2026-11-25T22:41:03.128+08:00","uptime_ms":1832450,
     "seq":42,"level":"info","origin":"agent","event":"guard.state",
     "guard":{"id":"...","name":"..."},"msg":"..."}

- 文件：每行一个 JSON，旧分片 gzip 压缩
- stderr：保持文本格式，供 procd / logread 使用
- 内存：保存结构化 dict，供本地 UI 快速查询
"""

import datetime
import gzip
import itertools
import json
import logging
import os
import re
import shutil
import time

LOG_SCHEMA_VERSION = 1
DEFAULT_MAX_BYTES = 2 * 1024 * 1024
DEFAULT_BACKUPS = 2
MAX_STRING = 4096
MAX_EVIDENCE = 1024

LEVEL_RANK = {"debug": 10, "info": 20, "warn": 30, "warning": 30, "error": 40, "critical": 50}

_STARTED = time.monotonic()
_SEQUENCE = itertools.count(1)
_CONTEXT = {"client_id": "", "boot_id": ""}

_SENSITIVE_KEYS = {
    "secret",
    "token",
    "password",
    "passwd",
    "authorization",
    "cookie",
    "webhook",
    "api_key",
    "apikey",
}
_REDACT_PATTERNS = (
    re.compile(r"(?i)(bearer)\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)((?:token|secret|password|webhook|api_key)=)[^&\s\"']+"),
)


def set_log_context(client_id="", boot_id=""):
    """设置会写入每条日志的上下文标识。"""
    _CONTEXT["client_id"] = str(client_id or "")
    _CONTEXT["boot_id"] = str(boot_id or "")


def reset_log_context():
    set_log_context("", "")


def _iso_timestamp(created):
    moment = datetime.datetime.fromtimestamp(created).astimezone()
    return moment.isoformat(timespec="milliseconds")


def _truncate(text, limit=MAX_STRING):
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _redact_text(text):
    result = str(text)
    for pattern in _REDACT_PATTERNS:
        result = pattern.sub(lambda match: match.group(1) + "***", result)
    return result


def _sanitize(value, key="", depth=0):
    if str(key).lower() in _SENSITIVE_KEYS:
        return "***"
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        cleaned = {}
        for item_key, item_value in value.items():
            name = str(item_key)
            if name.lower() in _SENSITIVE_KEYS:
                cleaned[name] = "***"
            else:
                cleaned[name] = _sanitize(item_value, name, depth + 1)
        if key in ("evidence", "payload"):
            serialized = json.dumps(cleaned, ensure_ascii=False)
            if len(serialized) > MAX_EVIDENCE:
                return {"_truncated": True, "text": _truncate(serialized, MAX_EVIDENCE)}
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, key, depth + 1) for item in value[:50]]
    if isinstance(value, str):
        return _truncate(_redact_text(value))
    return value


def record_to_event(record):
    """把 logging.LogRecord 转换为结构化日志 dict（同一 record 复用同一份结果）。"""
    cached = getattr(record, "_vigil_event", None)
    if isinstance(cached, dict):
        return cached

    extra = getattr(record, "vigil", None)
    extra = extra if isinstance(extra, dict) else {}
    event = str(extra.get("event") or "agent.log")
    message = record.getMessage()

    payload = {
        "v": LOG_SCHEMA_VERSION,
        "ts": _iso_timestamp(record.created),
        "uptime_ms": int((time.monotonic() - _STARTED) * 1000),
        "seq": next(_SEQUENCE),
        "level": record.levelname.lower(),
        "origin": "agent",
        "event": event,
        "logger": record.name,
        "msg": _truncate(_redact_text(message)),
    }
    if _CONTEXT["client_id"]:
        payload["client_id"] = _CONTEXT["client_id"]
    if _CONTEXT["boot_id"]:
        payload["boot_id"] = _CONTEXT["boot_id"]

    for key, value in extra.items():
        if key in ("event", "msg"):
            continue
        payload[key] = _sanitize(value, key)

    if record.exc_info:
        exc_type = record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
        payload["err"] = {
            "type": exc_type,
            "stack": _truncate(logging.Formatter().formatException(record.exc_info)),
        }
    record._vigil_event = payload
    return payload


class JsonlFormatter(logging.Formatter):
    """输出单行 JSON，字段顺序固定。"""

    def format(self, record):
        return json.dumps(record_to_event(record), ensure_ascii=False, separators=(",", ":"))


class MemoryLogHandler(logging.Handler):
    """把结构化日志同步到内存环形缓冲，供本地 API 查询。"""

    def __init__(self, state):
        super().__init__()
        self.state = state

    def emit(self, record):
        try:
            self.state.add_log(record_to_event(record))
        except Exception:  # 日志链路本身不能抛异常
            pass


class CompressingRotatingFileHandler(logging.FileHandler):
    """按大小轮转，并把旧分片压缩为 .1.gz / .2.gz。"""

    def __init__(
        self,
        filename,
        max_bytes=DEFAULT_MAX_BYTES,
        backup_count=DEFAULT_BACKUPS,
        compress=True,
        encoding="utf-8",
    ):
        super().__init__(filename, mode="a", encoding=encoding)
        self.max_bytes = int(max_bytes)
        self.backup_count = int(backup_count)
        self.compress = bool(compress)
        try:
            self._size = os.path.getsize(self.baseFilename)
        except OSError:
            self._size = 0
        try:
            os.chmod(self.baseFilename, 0o600)
        except OSError:
            pass

    def emit(self, record):
        try:
            message = self.format(record)
            self.stream.write(message + self.terminator)
            self.flush()
            self._size += len(message.encode("utf-8")) + len(self.terminator)
            if self.backup_count > 0 and self._size >= self.max_bytes:
                self.rotate()
        except Exception:
            self.handleError(record)

    def rotate(self):
        path = self.baseFilename
        try:
            self.stream.close()
        except OSError:
            pass

        try:
            self._shift_backups(path)
            if self.compress and self._gzip_current(path):
                pass
            else:
                os.replace(path, "%s.1" % path)
        except OSError:
            # 压缩或改名失败时保留当前文件，继续追加，避免丢日志
            pass

        try:
            self.stream = self._open()
            self._size = 0
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except OSError:
            self.stream = open(os.devnull, "w", encoding="utf-8")
            self._size = 0

    def _shift_backups(self, path):
        oldest = "%s.%s.gz" % (path, self.backup_count)
        if os.path.exists(oldest):
            os.remove(oldest)
        oldest_plain = "%s.%s" % (path, self.backup_count)
        if os.path.exists(oldest_plain):
            os.remove(oldest_plain)

        for index in range(self.backup_count - 1, 0, -1):
            for suffix in (".gz", ""):
                source = "%s.%s%s" % (path, index, suffix)
                target = "%s.%s%s" % (path, index + 1, suffix)
                if os.path.exists(source):
                    os.replace(source, target)

    def _gzip_current(self, path):
        temp_path = "%s.1.gz.tmp" % path
        try:
            with open(path, "rb") as source, open(temp_path, "wb") as raw:
                with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as archive:
                    shutil.copyfileobj(source, archive)
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, "%s.1.gz" % path)
            os.remove(path)
            return True
        except OSError:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass
            return False


def log_event(level, event, msg, **fields):
    """写入一条结构化日志，自动进入文件、stderr 与内存。"""
    numeric = LEVEL_RANK.get(str(level).lower(), logging.INFO)
    extra = {"event": str(event)}
    extra.update(fields)
    logging.getLogger("vigil.agent").log(numeric, str(msg), extra={"vigil": extra})


def _match_level(item_level, minimum):
    if not minimum:
        return True
    return LEVEL_RANK.get(str(item_level).lower(), 0) >= LEVEL_RANK.get(
        str(minimum).lower(), 0
    )


def _match_event(item, event_filter):
    if not event_filter:
        return True
    name = str(item.get("event") or "")
    return name == event_filter or name.startswith(str(event_filter))


def _match_guard(item, guard_id):
    if not guard_id:
        return True
    return str((item.get("guard") or {}).get("id") or "") == str(guard_id)


def _match_recovery(item, recovery_id):
    if not recovery_id:
        return True
    return str(item.get("recovery_id") or "") == str(recovery_id)


def _event_epoch(item):
    raw = item.get("ts")
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return datetime.datetime.fromisoformat(str(raw)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def filter_events(
    items,
    level=None,
    event=None,
    guard_id=None,
    recovery_id=None,
    since=None,
    limit=None,
):
    since_value = float(since) if since not in (None, "") else 0.0
    selected = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if not _match_level(item.get("level"), level):
            continue
        if not _match_event(item, event):
            continue
        if not _match_guard(item, guard_id):
            continue
        if not _match_recovery(item, recovery_id):
            continue
        if since_value and _event_epoch(item) < since_value:
            continue
        selected.append(item)
    if limit:
        selected = selected[-int(limit) :]
    return selected


def _iter_file_lines(path, reverse=False):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return
    if reverse:
        lines = list(reversed(lines))
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            yield item


def _iter_gzip_lines(path, reverse=False):
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return
    if reverse:
        lines = list(reversed(lines))
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            yield item


def _archive_paths(path, backup_count):
    for index in range(backup_count, 0, -1):
        compressed = "%s.%s.gz" % (path, index)
        plain = "%s.%s" % (path, index)
        if os.path.exists(compressed):
            yield compressed, True
        if os.path.exists(plain):
            yield plain, False


def _iter_sources(path, backup_count, newest_first=True):
    archives = list(_archive_paths(path, backup_count))
    if newest_first:
        yield path, False
        for archive, compressed in reversed(archives):
            yield archive, compressed
    else:
        for archive, compressed in archives:
            yield archive, compressed
        yield path, False


def read_log_events(
    path,
    limit=200,
    level=None,
    event=None,
    guard_id=None,
    recovery_id=None,
    since=None,
    backup_count=DEFAULT_BACKUPS,
):
    """从当前文件与 gzip 分片读取最近的日志（新→旧扫描，返回时间正序）。"""
    limit = max(1, int(limit))
    collected = []
    for source, compressed in _iter_sources(path, backup_count):
        iterator = (
            _iter_gzip_lines(source, reverse=True)
            if compressed
            else _iter_file_lines(source, reverse=True)
        )
        for item in iterator:
            if len(collected) >= limit:
                break
            if not filter_events(
                [item],
                level=level,
                event=event,
                guard_id=guard_id,
                recovery_id=recovery_id,
                since=since,
            ):
                continue
            collected.append(item)
        if len(collected) >= limit:
            break
    collected.reverse()
    return collected


def export_log_events(
    path,
    level=None,
    event=None,
    guard_id=None,
    recovery_id=None,
    since=None,
    max_lines=10000,
    max_bytes=5 * 1024 * 1024,
    backup_count=DEFAULT_BACKUPS,
):
    """按过滤条件导出 JSONL（旧→新），返回 bytes。"""
    output = []
    total = 0
    for source, compressed in _iter_sources(path, backup_count, newest_first=False):
        iterator = (
            _iter_gzip_lines(source) if compressed else _iter_file_lines(source)
        )
        for item in iterator:
            if not filter_events(
                [item],
                level=level,
                event=event,
                guard_id=guard_id,
                recovery_id=recovery_id,
                since=since,
            ):
                continue
            line = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
            size = len(line.encode("utf-8")) + 1
            if len(output) >= max_lines or total + size > max_bytes:
                return ("\n".join(output) + "\n").encode("utf-8") if output else b""
            output.append(line)
            total += size
    return ("\n".join(output) + "\n").encode("utf-8") if output else b""
