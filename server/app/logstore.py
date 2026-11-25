"""服务端日志核心：JSONL 格式化、按实例轮转、查询与导出。

api 与 worker 是独立进程，各自写入 `server-api.jsonl` / `server-worker.jsonl`，
避免多进程同时轮转同一个文件。读取时合并两个实例并按时间排序。
"""

from __future__ import annotations

import contextvars
import datetime
import gzip
import itertools
import json
import logging
import os
import re
import shutil
import time
from collections.abc import Iterable
from typing import Any

LOG_SCHEMA_VERSION = 1
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_BACKUPS = 5
MAX_STRING = 4096
MAX_EVIDENCE = 1024

LEVEL_RANK = {"debug": 10, "info": 20, "warn": 30, "warning": 30, "error": 40, "critical": 50}
INSTANCES = ("api", "worker")

_STARTED = time.monotonic()
_SEQUENCE = itertools.count(1)
_INSTANCE = "api"
_REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("vigil_request_id", default="")

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


def set_instance(instance: str) -> None:
    global _INSTANCE
    _INSTANCE = str(instance or "api")


def set_request_id(value: str) -> contextvars.Token:
    return _REQUEST_ID.set(str(value or ""))


def reset_request_id(token: contextvars.Token) -> None:
    _REQUEST_ID.reset(token)


def _iso_timestamp(created: float) -> str:
    return datetime.datetime.fromtimestamp(created).astimezone().isoformat(timespec="milliseconds")


def _truncate(text: Any, limit: int = MAX_STRING) -> str:
    value = str(text)
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"


def _redact_text(text: Any) -> str:
    result = str(text)
    for pattern in _REDACT_PATTERNS:
        result = pattern.sub(lambda match: match.group(1) + "***", result)
    return result


def _sanitize(value: Any, key: str = "", depth: int = 0) -> Any:
    if str(key).lower() in _SENSITIVE_KEYS:
        return "***"
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        cleaned = {}
        for item_key, item_value in value.items():
            name = str(item_key)
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


def record_to_event(record: logging.LogRecord) -> dict[str, Any]:
    cached = getattr(record, "_vigil_event", None)
    if isinstance(cached, dict):
        return cached

    extra = getattr(record, "vigil", None)
    extra = extra if isinstance(extra, dict) else {}
    event = str(extra.get("event") or "server.log")
    payload: dict[str, Any] = {
        "v": LOG_SCHEMA_VERSION,
        "ts": _iso_timestamp(record.created),
        "uptime_ms": int((time.monotonic() - _STARTED) * 1000),
        "seq": next(_SEQUENCE),
        "level": record.levelname.lower(),
        "origin": "server",
        "instance": _INSTANCE,
        "event": event,
        "logger": record.name,
        "msg": _truncate(_redact_text(record.getMessage())),
    }
    request_id = _REQUEST_ID.get()
    if request_id:
        payload["request_id"] = request_id
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
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(record_to_event(record), ensure_ascii=False, separators=(",", ":"))


class CompressingRotatingFileHandler(logging.FileHandler):
    """按大小轮转，旧分片 gzip；仅单进程使用。"""

    def __init__(
        self,
        filename: str,
        max_bytes: int = DEFAULT_MAX_BYTES,
        backup_count: int = DEFAULT_BACKUPS,
        compress: bool = True,
        encoding: str = "utf-8",
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

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            if self.stream is None:
                return
            self.stream.write(message + self.terminator)
            self.flush()
            self._size += len(message.encode("utf-8")) + len(self.terminator)
            if self.backup_count > 0 and self._size >= self.max_bytes:
                self.rotate()
        except Exception:
            self.handleError(record)

    def rotate(self) -> None:
        path = self.baseFilename
        try:
            if self.stream is not None:
                self.stream.close()
        except OSError:
            pass
        try:
            self._shift_backups(path)
            if not (self.compress and self._gzip_current(path)):
                os.replace(path, "%s.1" % path)
        except OSError:
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

    def _shift_backups(self, path: str) -> None:
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

    def _gzip_current(self, path: str) -> bool:
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


def log_event(level: str, event: str, msg: str, **fields: Any) -> None:
    numeric = LEVEL_RANK.get(str(level).lower(), logging.INFO)
    extra = {"event": str(event)}
    extra.update(fields)
    logging.getLogger("vigil.server").log(numeric, str(msg), extra={"vigil": extra})


def log_path_for(settings: Any, instance: str) -> str:
    return os.path.join(settings.log_dir, "server-%s.jsonl" % instance)


def log_paths(settings: Any) -> list[str]:
    return [log_path_for(settings, instance) for instance in INSTANCES]


def configure_logging(instance: str, settings: Any) -> str:
    """配置根日志：JSONL 文件 + 文本 stderr；重复调用会替换旧 handler。"""
    set_instance(instance)
    path = log_path_for(settings, instance)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        if getattr(handler, "_vigil_handler", False):
            root.removeHandler(handler)
            try:
                handler.close()
            except OSError:
                pass

    text_formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    console = logging.StreamHandler()
    console.setFormatter(text_formatter)
    console._vigil_handler = True  # type: ignore[attr-defined]
    root.addHandler(console)

    try:
        os.makedirs(settings.log_dir, exist_ok=True)
        file_handler = CompressingRotatingFileHandler(
            path,
            max_bytes=settings.log_max_bytes,
            backup_count=settings.log_backups,
            compress=settings.log_compress,
        )
        file_handler.setFormatter(JsonlFormatter())
        file_handler._vigil_handler = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)
    except OSError as exc:
        root.warning("无法写入服务端日志 %s：%s", path, exc)
    return path


def _match(item: dict[str, Any], level: str | None, event: str | None,
           client_id: str | None, request_id: str | None, instance: str | None,
           since: float | None) -> bool:
    if level and LEVEL_RANK.get(str(item.get("level", "")).lower(), 0) < LEVEL_RANK.get(
        str(level).lower(), 0
    ):
        return False
    if event:
        name = str(item.get("event") or "")
        if name != event and not name.startswith(str(event)):
            return False
    if client_id and str(item.get("client_id") or "") != str(client_id):
        return False
    if request_id and str(item.get("request_id") or "") != str(request_id):
        return False
    if instance and str(item.get("instance") or "") != str(instance):
        return False
    if since:
        try:
            epoch = datetime.datetime.fromisoformat(str(item.get("ts"))).timestamp()
        except (TypeError, ValueError):
            epoch = 0.0
        if epoch < float(since):
            return False
    return True


def _iter_file(path: str, compressed: bool = False) -> Iterable[dict[str, Any]]:
    opener = gzip.open if compressed else open
    try:
        with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict):
                    yield item
    except OSError:
        return


def _iter_sources(path: str, backups: int) -> Iterable[tuple[str, bool]]:
    for index in range(backups, 0, -1):
        for suffix, compressed in ((".gz", True), ("", False)):
            candidate = "%s.%s%s" % (path, index, suffix)
            if os.path.exists(candidate):
                yield candidate, compressed
    yield path, False


def collect_log_events(
    paths: Iterable[str],
    *,
    limit: int = 200,
    level: str | None = None,
    event: str | None = None,
    client_id: str | None = None,
    request_id: str | None = None,
    instance: str | None = None,
    since: float | None = None,
    backups: int = DEFAULT_BACKUPS,
) -> list[dict[str, Any]]:
    """合并多个实例日志，按时间升序返回最近 limit 条。"""
    items: list[dict[str, Any]] = []
    for path in paths:
        for source, compressed in _iter_sources(path, backups):
            for item in _iter_file(source, compressed):
                if _match(item, level, event, client_id, request_id, instance, since):
                    items.append(item)
    items.sort(key=lambda item: str(item.get("ts") or ""))
    return items[-max(1, int(limit)) :]


def export_log_events(
    paths: Iterable[str],
    *,
    level: str | None = None,
    event: str | None = None,
    client_id: str | None = None,
    request_id: str | None = None,
    instance: str | None = None,
    since: float | None = None,
    max_lines: int = 10000,
    max_bytes: int = 5 * 1024 * 1024,
    backups: int = DEFAULT_BACKUPS,
) -> bytes:
    items: list[dict[str, Any]] = []
    for path in paths:
        for source, compressed in _iter_sources(path, backups):
            for item in _iter_file(source, compressed):
                if _match(item, level, event, client_id, request_id, instance, since):
                    items.append(item)
    items.sort(key=lambda item: str(item.get("ts") or ""))
    output: list[str] = []
    total = 0
    for item in items:
        line = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        size = len(line.encode("utf-8")) + 1
        if len(output) >= max_lines or total + size > max_bytes:
            break
        output.append(line)
        total += size
    return ("\n".join(output) + "\n").encode("utf-8") if output else b""
