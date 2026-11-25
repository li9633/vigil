"""数据库连接与会话管理（SQLAlchemy 2.0 同步 ORM + SQLite）。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def utcnow() -> datetime:
    """当前 UTC 时间（naive），与 SQLite 存储格式保持一致。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


def _set_sqlite_pragma(dbapi_connection: Any, _connection_record: Any) -> None:
    """API 与 worker 双进程访问 SQLite，开启 WAL 并设置忙等待。"""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def init_engine(database_url: str) -> Engine:
    """初始化全局引擎；重复调用会释放旧引擎（测试用）。"""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    connect_args: dict[str, Any] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    _engine = create_engine(database_url, connect_args=connect_args)
    if database_url.startswith("sqlite"):
        event.listen(_engine, "connect", _set_sqlite_pragma)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("数据库引擎尚未初始化，请先调用 init_engine()")
    return _engine


def init_db() -> None:
    """建表并做轻量列迁移（不使用 Alembic）。"""
    # 先导入模型以完成元数据注册
    from app import models  # noqa: F401

    engine = get_engine()
    Base.metadata.create_all(engine)

    # SQLite 不支持 create_all 补列，这里做一次幂等迁移
    from sqlalchemy import inspect, text

    with engine.begin() as connection:
        columns = {item["name"] for item in inspect(connection).get_columns("heartbeats")}
        if "guards_json" not in columns:
            connection.execute(
                text("ALTER TABLE heartbeats ADD COLUMN guards_json TEXT DEFAULT '{}'")
            )

        incident_columns = {
            item["name"] for item in inspect(connection).get_columns("incidents")
        }
        if "guard_id" not in incident_columns:
            connection.execute(
                text("ALTER TABLE incidents ADD COLUMN guard_id VARCHAR(64)")
            )
        if "guard_name" not in incident_columns:
            connection.execute(
                text("ALTER TABLE incidents ADD COLUMN guard_name VARCHAR(128)")
            )


def _session_factory_or_raise() -> sessionmaker[Session]:
    if _session_factory is None:
        raise RuntimeError("数据库会话工厂尚未初始化，请先调用 init_engine()")
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """脚本/worker 用会话上下文，正常退出提交，异常回滚。"""
    session = _session_factory_or_raise()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI 依赖：每个请求一个会话，路由内自行提交。"""
    session = _session_factory_or_raise()()
    try:
        yield session
    finally:
        session.close()
