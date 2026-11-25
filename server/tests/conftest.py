"""pytest 公共夹具。"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from app.db import init_db, init_engine
from app.main import create_app
from app.settings import Settings

BOOTSTRAP_TOKEN = "test-bootstrap"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "test-pass"


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    db_path = (tmp_path / "vigil-test.db").as_posix()
    return Settings(
        database_url=f"sqlite+pysqlite:///{db_path}",
        bootstrap_token=BOOTSTRAP_TOKEN,
        admin_username=ADMIN_USERNAME,
        admin_password=ADMIN_PASSWORD,
        session_secret="test-session-secret",
    )


@pytest.fixture()
def db_ready(settings: Settings) -> None:
    """初始化引擎并建表，供纯状态机测试使用。"""
    init_engine(settings.database_url)
    init_db()


@pytest.fixture()
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture()
def auth() -> Callable[..., dict[str, str]]:
    def _make(
        client_id: str,
        secret: str,
        *,
        nonce: str | None = None,
        timestamp: int | None = None,
    ) -> dict[str, str]:
        return {
            "X-Vigil-Client": client_id,
            "Authorization": f"Bearer {secret}",
            "X-Vigil-Timestamp": str(int(time.time()) if timestamp is None else timestamp),
            "X-Vigil-Nonce": nonce or str(uuid.uuid4()),
        }

    return _make


@pytest.fixture()
def registered(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/v1/register",
        headers={"X-Vigil-Bootstrap": BOOTSTRAP_TOKEN},
        json={
            "name": "test-router",
            "model": "iStoreOS",
            "fw_version": "24.10",
            "agent_version": "0.1.0",
            "protocol": 1,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()
