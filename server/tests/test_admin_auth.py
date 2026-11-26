"""管理员密码哈希、随机密钥、初始密码与命令行重置。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import admin_cli
from app.db import session_scope
from app.settings import Settings
from app.system_settings import (
    admin_auth_state,
    ensure_admin_auth,
    hash_password,
    load_admin_auth,
    load_pepper,
    rotate_pepper,
    verify_admin_password,
    verify_password,
)


def _seed(settings: Settings) -> str:
    with session_scope() as db:
        result = ensure_admin_auth(db, settings)
    assert result["created"] is True
    return Path(result["initial_password_file"]).read_text(encoding="utf-8").strip()


def _set_env(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setenv("VIGIL_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("VIGIL_ADMIN_KEY_FILE", settings.admin_key_file)
    monkeypatch.setenv("VIGIL_LOG_DIR", settings.log_dir)


def test_initial_password_and_key_are_generated(settings: Settings, db_ready: None) -> None:
    password = _seed(settings)
    assert len(password) >= 8
    key_path = Path(settings.admin_key_file)
    assert key_path.exists()
    first = load_pepper(settings.admin_key_file)
    assert len(first) == 32
    assert load_pepper(settings.admin_key_file) == first

    with session_scope() as db:
        record = load_admin_auth(db)
        assert record is not None
        assert record["key_id"]
        assert verify_admin_password(db, password, settings.admin_key_file)
        assert not verify_admin_password(db, "wrong-pass", settings.admin_key_file)


def test_hash_requires_matching_pepper() -> None:
    pepper = b"a" * 32
    record = hash_password("secret-123", pepper=pepper)
    assert verify_password("secret-123", record, pepper=pepper)
    assert not verify_password("wrong", record, pepper=pepper)
    assert not verify_password("secret-123", record, pepper=b"b" * 32)


def test_legacy_record_without_key_id_still_verifies() -> None:
    record = hash_password("secret-123")
    assert not record["key_id"]
    assert verify_password("secret-123", record)
    assert verify_password("secret-123", record, pepper=b"a" * 32)


def test_key_mismatch_is_reported(settings: Settings, db_ready: None) -> None:
    password = _seed(settings)
    rotate_pepper(settings.admin_key_file)

    with session_scope() as db:
        state = admin_auth_state(db, settings.admin_key_file)
        assert "source" not in state
        assert state["key_ok"] is False
        assert not verify_admin_password(db, password, settings.admin_key_file)


def test_cli_reset_password(
    settings: Settings,
    db_ready: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old_password = _seed(settings)
    _set_env(monkeypatch, settings)

    assert admin_cli.main(["reset-password", "--password", "new-pass-123"]) == 0
    assert "已重置" in capsys.readouterr().out

    with session_scope() as db:
        assert verify_admin_password(db, "new-pass-123", settings.admin_key_file)
        assert not verify_admin_password(db, old_password, settings.admin_key_file)


def test_cli_reset_key_invalidates_old_password(
    settings: Settings,
    db_ready: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed(settings)
    old_pepper = load_pepper(settings.admin_key_file)
    _set_env(monkeypatch, settings)

    assert admin_cli.main(["reset-key", "--password", "fresh-pass-123"]) == 0
    assert "旧密码已失效" in capsys.readouterr().out
    assert load_pepper(settings.admin_key_file) != old_pepper

    with session_scope() as db:
        assert verify_admin_password(db, "fresh-pass-123", settings.admin_key_file)
        assert not verify_admin_password(db, "new-pass-123", settings.admin_key_file)
