"""管理员认证用例：登录限流、凭据校验、审计与事务提交。

失败以领域异常表达，由 controller 映射为 HTTP 状态码。
"""

from __future__ import annotations

import threading
import time

from sqlalchemy.orm import Session

from app.errors import RateLimitedError, UnauthorizedError
from app.logstore import log_event
from app.repositories.audit_repo import add_audit
from app.security import constant_time_equals
from app.settings import Settings
from app.system_settings import verify_admin_password

LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 60
MAX_TRACKED_KEYS = 512


class LoginBlocked(RateLimitedError):
    """窗口内失败次数达到上限。"""

    def __init__(self, retry_after: int) -> None:
        super().__init__("登录尝试过于频繁，请稍后再试", retry_after=retry_after)


class InvalidCredentials(UnauthorizedError):
    """用户名或密码错误。"""

    def __init__(self) -> None:
        super().__init__("用户名或密码错误")


class LoginThrottle:
    """按来源 IP 的滑动窗口失败计数（进程内，线程安全）。"""

    def __init__(
        self,
        max_failures: int = LOGIN_MAX_FAILURES,
        window_seconds: int = LOGIN_WINDOW_SECONDS,
    ) -> None:
        self._max_failures = max_failures
        self._window_seconds = window_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, now: float) -> int | None:
        """返回 None 表示放行；否则返回建议的 Retry-After 秒数。"""
        with self._lock:
            stamps = [
                stamp
                for stamp in self._failures.get(key, [])
                if now - stamp < self._window_seconds
            ]
            if stamps:
                self._failures[key] = stamps
            else:
                self._failures.pop(key, None)
            if len(self._failures) > MAX_TRACKED_KEYS:
                self._prune(now)
            if len(stamps) >= self._max_failures:
                return max(1, int(self._window_seconds - (now - stamps[0])))
            return None

    def record_failure(self, key: str, now: float) -> None:
        with self._lock:
            self._failures.setdefault(key, []).append(now)

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()

    def _prune(self, now: float) -> None:
        expired = [
            key
            for key, stamps in self._failures.items()
            if not stamps or now - stamps[-1] >= self._window_seconds
        ]
        for key in expired:
            self._failures.pop(key, None)


login_throttle = LoginThrottle()


def reset_login_limiter() -> None:
    """清空限流状态，供测试使用。"""
    login_throttle.reset()


def login_admin(
    db: Session,
    settings: Settings,
    *,
    username: str,
    password: str,
    client_key: str,
    now: float | None = None,
) -> str:
    """校验管理员凭据并写入审计；成功返回 actor。"""
    timestamp = time.time() if now is None else now

    retry_after = login_throttle.check(client_key, timestamp)
    if retry_after is not None:
        add_audit(
            db,
            actor=username,
            action="login_blocked",
            target="admin",
            detail={"ip": client_key, "retry_after": retry_after},
        )
        db.commit()
        log_event(
            "warn",
            "auth.login.blocked",
            "管理员登录被限流",
            username=username,
            ip=client_key,
        )
        raise LoginBlocked(retry_after)

    valid = constant_time_equals(username, settings.admin_username) and (
        verify_admin_password(db, password, settings.admin_key_file)
    )
    if not valid:
        login_throttle.record_failure(client_key, timestamp)
        add_audit(
            db,
            actor=username,
            action="login_fail",
            target="admin",
            detail={"ip": client_key},
        )
        db.commit()
        log_event(
            "warn",
            "auth.login.fail",
            "管理员登录失败",
            username=username,
            ip=client_key,
        )
        raise InvalidCredentials

    login_throttle.clear(client_key)
    add_audit(db, actor=username, action="login", target="admin", detail={"ip": client_key})
    db.commit()
    log_event("info", "auth.login.success", "管理员登录成功", actor=username)
    return username
