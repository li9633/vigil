"""系统设置：管理员密码哈希（PBKDF2 + 随机 pepper）与运行时状态。

密码只在数据库中保存不可逆哈希，进程内不做明文比对之外的任何解密。
首次启动生成随机初始密码与随机 pepper；pepper 与数据库分开保存，
即使数据库泄露，没有密钥文件也无法离线校验密码。密钥丢失或需要轮换时，
使用 `vigil-admin reset-key` 重新生成密钥并同时重置密码。
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.db import utcnow
from app.repositories import setting_repo
from app.settings import Settings

LOGGER = logging.getLogger("vigil.system")

SETTING_KEY = "admin_auth"
ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 300_000
SALT_BYTES = 16
KEY_BYTES = 32
INITIAL_PASSWORD_FILE = "initial_admin_password"

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128


class AdminKeyError(RuntimeError):
    """管理员密码密钥文件不可用。"""


def key_id(pepper: bytes) -> str:
    """密钥指纹：写入记录用于快速发现密钥文件被替换。"""
    return hashlib.sha256(pepper).hexdigest()[:16]


def load_pepper(path: str, *, create: bool = False) -> bytes:
    """读取密钥；create=True 时在缺失情况下原子生成（0600）。"""
    file_path = Path(path)
    try:
        data = file_path.read_bytes()
    except FileNotFoundError:
        if not create:
            raise AdminKeyError("管理员密钥文件不存在：%s" % path) from None
        return _create_pepper(file_path)
    except OSError as exc:
        raise AdminKeyError("读取管理员密钥失败：%s" % exc) from exc

    if not data:
        raise AdminKeyError("管理员密钥为空：%s" % path)
    return data


def _create_pepper(file_path: Path) -> bytes:
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(file_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        data = file_path.read_bytes()
        if not data:
            raise AdminKeyError("管理员密钥为空：%s" % file_path) from None
        return data
    except OSError as exc:
        raise AdminKeyError("创建管理员密钥失败：%s" % exc) from exc

    pepper = secrets.token_bytes(KEY_BYTES)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(pepper)
    return pepper


def rotate_pepper(path: str) -> bytes:
    """生成并原子替换密钥文件，返回新密钥。"""
    file_path = Path(path)
    pepper = secrets.token_bytes(KEY_BYTES)
    temporary = file_path.with_name("%s.%s.tmp" % (file_path.name, secrets.token_hex(4)))
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(pepper)
        os.replace(temporary, file_path)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise AdminKeyError("写入管理员密钥失败：%s" % exc) from exc
    return pepper


def hash_password(
    password: str,
    *,
    pepper: bytes = b"",
    salt: str | None = None,
    iterations: int = ITERATIONS,
) -> dict[str, Any]:
    salt_hex = salt or secrets.token_hex(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8") + pepper,
        bytes.fromhex(salt_hex),
        iterations,
    )
    return {
        "algorithm": ALGORITHM,
        "iterations": iterations,
        "salt": salt_hex,
        "hash": digest.hex(),
        "key_id": key_id(pepper) if pepper else "",
    }


def verify_password(password: str, record: dict[str, Any], *, pepper: bytes = b"") -> bool:
    try:
        algorithm = str(record.get("algorithm") or "")
        iterations = int(record.get("iterations") or 0)
        salt = str(record.get("salt") or "")
        expected = str(record.get("hash") or "")
        stored_key = str(record.get("key_id") or "")
        if algorithm != ALGORITHM or iterations <= 0 or not salt or not expected:
            return False
        if stored_key and (not pepper or not secrets.compare_digest(stored_key, key_id(pepper))):
            return False

        candidates = [pepper]
        # 旧记录没有 key_id，兼容先前不带 pepper 的哈希
        if not stored_key:
            candidates.append(b"")
        salt_bytes = bytes.fromhex(salt)
        for candidate in candidates:
            computed = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8") + candidate,
                salt_bytes,
                iterations,
            ).hex()
            if secrets.compare_digest(computed, expected):
                return True
    except (TypeError, ValueError):
        return False
    return False


def load_admin_auth(db: Session) -> dict[str, Any] | None:
    return setting_repo.get_json(db, SETTING_KEY)


def set_admin_password(
    db: Session, password: str, pepper: bytes | None = None
) -> dict[str, Any]:
    """写入新的 PBKDF2 记录并落库（调用方负责提交）。"""
    record = hash_password(password, pepper=pepper or b"")
    record["updated_at"] = utcnow().isoformat() + "Z"
    setting_repo.set_json(db, SETTING_KEY, record)
    return record


def verify_admin_password(db: Session, password: str, key_file: str) -> bool:
    """只用数据库中的哈希校验密码；未初始化或密钥缺失时一律失败。"""
    record = load_admin_auth(db)
    if record is None:
        return False
    try:
        pepper = load_pepper(key_file)
    except AdminKeyError as exc:
        LOGGER.error("%s；请执行 vigil-admin reset-key 重置密钥和密码", exc)
        return False
    return verify_password(password, record, pepper=pepper)


def new_password() -> str:
    """生成一次性可展示的随机密码。"""
    return secrets.token_urlsafe(12)


def initial_password_path(key_file: str) -> str:
    return str(Path(key_file).with_name(INITIAL_PASSWORD_FILE))


def write_initial_password(key_file: str, password: str) -> str:
    """首次启动把随机密码写入 0600 文件，供管理员首次登录后立即修改。"""
    path = Path(initial_password_path(key_file))
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(password + "\n")
    return str(path)


def remove_initial_password(key_file: str) -> None:
    Path(initial_password_path(key_file)).unlink(missing_ok=True)


def ensure_admin_auth(db: Session, settings: Settings) -> dict[str, Any]:
    """首次启动生成随机密码与密钥并写入数据库；之后数据库是唯一来源。"""
    record = load_admin_auth(db)
    if record is not None:
        stored_key = str(record.get("key_id") or "")
        if stored_key:
            try:
                pepper = load_pepper(settings.admin_key_file)
            except AdminKeyError as exc:
                LOGGER.error("%s；管理员登录将失败，请执行 vigil-admin reset-key", exc)
            else:
                if not secrets.compare_digest(stored_key, key_id(pepper)):
                    LOGGER.error(
                        "管理员密钥与数据库记录不匹配；请执行 vigil-admin reset-key 重置"
                    )
        return {"created": False}

    pepper = load_pepper(settings.admin_key_file, create=True)
    password = new_password()
    set_admin_password(db, password, pepper)
    db.commit()
    path = write_initial_password(settings.admin_key_file, password)
    LOGGER.warning("初始管理员密码已生成：%s；登录后请立即修改", path)
    return {"created": True, "initial_password_file": path}


def admin_auth_state(db: Session, key_file: str | None = None) -> dict[str, Any]:
    record = load_admin_auth(db)
    if record is None:
        return {"updated_at": None, "key_id": "", "key_ok": None}
    state: dict[str, Any] = {
        "updated_at": record.get("updated_at"),
        "key_id": record.get("key_id") or "",
        "key_ok": None,
    }
    stored_key = str(record.get("key_id") or "")
    if key_file and stored_key:
        try:
            state["key_ok"] = secrets.compare_digest(
                stored_key, key_id(load_pepper(key_file))
            )
        except AdminKeyError:
            state["key_ok"] = False
    return state
