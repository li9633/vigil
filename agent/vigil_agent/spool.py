"""本地事件 spool：有界 jsonl，按 seq 顺序补传。

容量上限默认 500 条 / 2 MB；溢出时丢弃最旧记录并累计 dropped 计数。
文件采用追加写，ack 后整文件原子重写。
"""

import json
import os
import tempfile
import threading
import time
import uuid

DEFAULT_MAX_ENTRIES = 500
DEFAULT_MAX_BYTES = 2 * 1024 * 1024


class EventSpool:
    """单进程使用的有界事件队列。"""

    def __init__(self, spool_dir, max_entries=DEFAULT_MAX_ENTRIES, max_bytes=DEFAULT_MAX_BYTES):
        self.spool_dir = spool_dir
        self.max_entries = int(max_entries)
        self.max_bytes = int(max_bytes)
        self.path = os.path.join(spool_dir, "events.jsonl")
        self.meta_path = os.path.join(spool_dir, "meta.json")
        self._lock = threading.RLock()
        os.makedirs(spool_dir, exist_ok=True)
        self._meta = self._load_meta()
        self._entries, repaired = self._load_entries()
        if repaired:
            self._rewrite()
            self._save_meta()

    # ---------- 加载与持久化 ----------

    def _load_meta(self):
        meta = {"schema_version": 1, "seq": 0, "dropped": 0}
        try:
            with open(self.meta_path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
        except (OSError, ValueError):
            return meta
        if isinstance(loaded, dict):
            for key in ("schema_version", "seq", "dropped"):
                value = loaded.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    meta[key] = value
        return meta

    def _save_meta(self):
        directory = os.path.dirname(self.meta_path) or "."
        os.makedirs(directory, exist_ok=True)
        tmp_path = self.meta_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(self._meta, handle, ensure_ascii=False)
            os.replace(tmp_path, self.meta_path)
        except OSError:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _load_entries(self):
        entries = []
        repaired = False
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except OSError:
            return entries, False

        max_seq = self._meta.get("seq", 0)
        for line in lines:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                repaired = True
                continue
            if not isinstance(entry, dict) or not isinstance(entry.get("seq"), int):
                repaired = True
                continue
            entries.append(entry)
            max_seq = max(max_seq, entry["seq"])
        entries.sort(key=lambda item: item["seq"])
        if max_seq > self._meta.get("seq", 0):
            self._meta["seq"] = max_seq
        return entries, repaired

    def _rewrite(self):
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".events-", dir=directory, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                for entry in self._entries:
                    handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
                    handle.write("\n")
            os.replace(tmp_path, self.path)
        except OSError:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _bytes(self):
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0

    # ---------- 对外接口 ----------

    def next_seq(self, persist=True):
        """分配一个全局单调序号（心跳与事件共用）。

        persist=False 用于心跳等高频场景，避免频繁写 overlay；事件落盘时
        仍会持久化当前序号，重启后不会低于最近一条事件。
        """
        with self._lock:
            self._meta["seq"] = self._meta.get("seq", 0) + 1
            if persist:
                self._save_meta()
            return self._meta["seq"]

    def append(self, event):
        """追加事件，返回落盘后的副本（补全 seq / event_id / ts）。"""
        if not isinstance(event, dict):
            raise TypeError("事件必须是对象")
        with self._lock:
            entry = dict(event)
            provided = entry.get("seq")
            if isinstance(provided, int) and provided > self._meta.get("seq", 0):
                seq = provided
            else:
                seq = self._meta.get("seq", 0) + 1
            self._meta["seq"] = seq
            entry["seq"] = seq
            if not entry.get("event_id"):
                entry["event_id"] = uuid.uuid4().hex
            entry.setdefault("ts", int(time.time()))

            line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line)
                handle.write("\n")
            self._entries.append(entry)
            self._enforce_limits()
            self._save_meta()
            return dict(entry)

    def _enforce_limits(self):
        dropped = 0
        while len(self._entries) > self.max_entries or (
            self._bytes() > self.max_bytes and len(self._entries) > 1
        ):
            self._entries.pop(0)
            dropped += 1
        if dropped:
            self._meta["dropped"] = self._meta.get("dropped", 0) + dropped
            self._rewrite()

    def pending(self, limit=50):
        """按 seq 升序返回待上传事件。"""
        with self._lock:
            return [dict(entry) for entry in self._entries[: max(0, int(limit))]]

    def ack(self, seqs):
        """确认已上传的序号并删除对应记录，返回删除条数。"""
        acked = set()
        for seq in seqs or []:
            if isinstance(seq, int) and not isinstance(seq, bool):
                acked.add(seq)
        if not acked:
            return 0
        with self._lock:
            remaining = [entry for entry in self._entries if entry.get("seq") not in acked]
            removed = len(self._entries) - len(remaining)
            if removed:
                self._entries = remaining
                self._rewrite()
                self._save_meta()
            return removed

    def read(self, limit=None, newest_first=False):
        """读取本地事件（供 UI 展示），默认按时间正序。"""
        with self._lock:
            entries = list(self._entries)
        if limit is not None:
            limit = max(0, int(limit))
            entries = entries[-limit:] if limit else []
        if newest_first:
            entries = list(reversed(entries))
        return [dict(entry) for entry in entries]

    def stats(self):
        with self._lock:
            return {
                "pending": len(self._entries),
                "dropped": int(self._meta.get("dropped", 0)),
                "bytes": self._bytes(),
                "max_entries": self.max_entries,
                "max_bytes": self.max_bytes,
            }
