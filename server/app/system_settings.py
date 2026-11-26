"""系统设置：管理员密码的运行时覆盖（PBKDF2）与读取。

管理员初始密码来自环境变量；一旦在服务端 UI 修改密码，就以
`Setting(key='admin_auth')` 中的 PBKDF2 记录为准，环境变量作为回退。
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

from sqlalchemy.orm import Session

from app.db import utcnow
from app.repositories import setting_repo

SETTING_KEY = "admin_auth"
ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 300_000
SALT_BYTES = 16
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128


def hash_password(
    password: str, *, salt: str | None = None, iterations: int = ITERATIONS
) -> dict[str, Any]:
    salt_hex = salt or secrets.token_hex(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), iterations
    )
    return {
        "algorithm": ALGORITHM,
        "iterations": iterations,
        "salt": salt_hex,
        "hash": digest.hex(),
    }


def verify_password(password: str, record: dict[str, Any]) -> bool:
    try:
        algorithm = str(record.get("algorithm") or "")
        iterations = int(record.get("iterations") or 0)
        salt = str(record.get("salt") or "")
        expected = str(record.get("hash") or "")
        if algorithm != ALGORITHM or iterations <= 0 or not salt or not expected:
            return False
        computed = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations
        ).hex()
    except (TypeError, ValueError):
        return False
    return secrets.compare_digest(computed, expected)


def load_admin_auth(db: Session) -> dict[str, Any] | None:
    return setting_repo.get_json(db, SETTING_KEY)


def set_admin_password(db: Session, password: str) -> dict[str, Any]:
    """写入新的 PBKDF2 记录并落库（调用方负责提交）。"""
    record = hash_password(password)
    record["updated_at"] = utcnow().isoformat() + "Z"
    setting_repo.set_json(db, SETTING_KEY, record)
    return record


def verify_admin_password(db: Session, env_password: str, password: str) -> bool:
    """数据库覆盖优先，未设置时回退环境变量密码。"""
    record = load_admin_auth(db)
    if record is None:
        return secrets.compare_digest(
            password.encode("utf-8"), env_password.encode("utf-8")
        )
    return verify_password(password, record)


def admin_auth_state(db: Session) -> dict[str, Any]:
    record = load_admin_auth(db)
    if record is None:
        return {"source": "env", "updated_at": None}
    return {"source": "database", "updated_at": record.get("updated_at")}
