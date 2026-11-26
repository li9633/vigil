"""数据库轻量迁移：V1 heartbeats.modules_json 必须被移除。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, text

from app.db import get_engine, init_db, init_engine


def test_legacy_heartbeats_modules_column_is_dropped(tmp_path: Path) -> None:
    db_path = (tmp_path / "legacy.db").as_posix()
    engine = init_engine(f"sqlite+pysqlite:///{db_path}")

    # 模拟 V1 表结构：modules_json NOT NULL 且没有 V2 新列
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE heartbeats ("
                "id INTEGER PRIMARY KEY, "
                "client_id VARCHAR(64), "
                "received_at DATETIME, "
                "seq INTEGER, "
                "uptime INTEGER, "
                "boot_id VARCHAR(64), "
                "wan_ip VARCHAR(64), "
                "modules_json TEXT NOT NULL DEFAULT '{}', "
                "guards_json TEXT NOT NULL DEFAULT '{}'"
                ")"
            )
        )

    init_db()

    with engine.begin() as connection:
        columns = {item["name"] for item in inspect(connection).get_columns("heartbeats")}
        assert "modules_json" not in columns
        assert {"checks_json", "probes_json", "guards_json"} <= columns
        # 新结构可以正常插入（旧 NOT NULL 列已不存在）
        connection.execute(
            text(
                "INSERT INTO heartbeats "
                "(client_id, received_at, seq, uptime, boot_id, wan_ip, "
                "checks_json, probes_json, guards_json) "
                "VALUES ('c1', '2026-11-26 00:00:00', 1, 1, 'b1', NULL, '{}', '[]', '{}')"
            )
        )


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    db_path = (tmp_path / "twice.db").as_posix()
    engine = init_engine(f"sqlite+pysqlite:///{db_path}")
    init_db()
    init_db()
    columns = {item["name"] for item in inspect(engine).get_columns("clients")}
    assert "probe_catalog_json" in columns
    assert get_engine() is engine
