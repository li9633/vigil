"""服务端日志核心测试。"""

from __future__ import annotations

import datetime
import gzip
import json
import logging
import os
import tempfile

from app.logstore import (
    CompressingRotatingFileHandler,
    JsonlFormatter,
    collect_log_events,
    export_log_events,
    record_to_event,
    reset_request_id,
    set_instance,
    set_request_id,
)


def _record(message="hello", **extra):
    record = logging.LogRecord("vigil.server", logging.INFO, __file__, 1, message, (), None)
    record.vigil = {"event": "test.event", **extra}
    return record


def test_server_envelope_has_instance_and_request_id():
    set_instance("worker")
    token = set_request_id("req-123")
    try:
        event = record_to_event(_record(client_id="cl_1"))
    finally:
        reset_request_id(token)
        set_instance("api")

    assert event["origin"] == "server"
    assert event["instance"] == "worker"
    assert event["request_id"] == "req-123"
    assert event["client_id"] == "cl_1"
    assert event["v"] == 1


def test_server_redaction():
    event = record_to_event(
        _record(
            message="auth Bearer abc123",
            secret="top",
            webhook="https://example.com/hook",
            note="token=abcdef&x=1",
        )
    )
    assert event["secret"] == "***"
    assert event["webhook"] == "***"
    assert "abc123" not in event["msg"]
    assert "abcdef" not in event["note"]


def _write_events(path, items):
    with open(path, "w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def test_collect_merges_instances_and_filters():
    with tempfile.TemporaryDirectory() as root:
        api_path = os.path.join(root, "server-api.jsonl")
        worker_path = os.path.join(root, "server-worker.jsonl")
        _write_events(
            api_path,
            [
                {
                    "ts": "2026-01-01T00:00:00.000+08:00",
                    "level": "info",
                    "instance": "api",
                    "event": "api.request",
                    "client_id": "cl_1",
                    "request_id": "req-a",
                },
                {
                    "ts": "2026-01-01T00:00:02.000+08:00",
                    "level": "error",
                    "instance": "api",
                    "event": "server.error",
                },
            ],
        )
        _write_events(
            worker_path,
            [
                {
                    "ts": "2026-01-01T00:00:01.000+08:00",
                    "level": "warn",
                    "instance": "worker",
                    "event": "incident.open",
                    "client_id": "cl_1",
                }
            ],
        )

        items = collect_log_events([api_path, worker_path], limit=10)
        assert [item["instance"] for item in items] == ["api", "worker", "api"]

        assert len(collect_log_events([api_path, worker_path], level="warn")) == 2
        assert len(collect_log_events([api_path, worker_path], event="incident.")) == 1
        assert len(collect_log_events([api_path, worker_path], client_id="cl_1")) == 2
        assert len(collect_log_events([api_path, worker_path], request_id="req-a")) == 1
        assert len(collect_log_events([api_path, worker_path], instance="worker")) == 1

        since = datetime.datetime.fromisoformat("2026-01-01T00:00:01.500+08:00").timestamp()
        assert [item["level"] for item in collect_log_events([api_path, worker_path], since=since)] == [
            "error"
        ]


def test_export_is_chronological():
    with tempfile.TemporaryDirectory() as root:
        path = os.path.join(root, "server-api.jsonl")
        _write_events(
            path,
            [
                {"ts": "2026-01-01T00:00:02.000+08:00", "level": "info", "event": "b"},
                {"ts": "2026-01-01T00:00:01.000+08:00", "level": "info", "event": "a"},
            ],
        )
        data = export_log_events([path])
        events = [json.loads(line) for line in data.decode().splitlines()]
        assert [item["event"] for item in events] == ["a", "b"]


def test_rotation_compresses(tmp_path):
    path = tmp_path / "server-api.jsonl"
    logger = logging.getLogger("test.server.rotation")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = CompressingRotatingFileHandler(
        str(path), max_bytes=400, backup_count=2, compress=True
    )
    handler.setFormatter(JsonlFormatter())
    logger.addHandler(handler)
    try:
        for index in range(30):
            logger.info(
                "line %s",
                index,
                extra={"vigil": {"event": "test.line", "index": index}},
            )
    finally:
        handler.close()
        logger.removeHandler(handler)

    assert (tmp_path / "server-api.jsonl.1.gz").exists()
    with gzip.open(tmp_path / "server-api.jsonl.1.gz", "rt", encoding="utf-8") as handle:
        first = json.loads(handle.readline())
    assert first["event"] == "test.line"
    assert first["origin"] == "server"
