"""运行时配置：全部来自环境变量，带开发默认值。"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from fastapi import Request

from .utils.env import env_bool as _env_bool
from .utils.env import env_int as _env_int
from .utils.env import env_str as _env_str


@dataclass(frozen=True)
class Thresholds:
    """状态机阈值，单位秒。"""

    heartbeat_interval: int = 30
    suspect_seconds: int = 90
    down_seconds: int = 150
    beacon_seconds: int = 180


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite+pysqlite:///./vigil.db"
    bootstrap_token: str = "vigil-bootstrap-dev"
    admin_username: str = "admin"
    admin_key_file: str = "/var/lib/vigil/admin.key"
    session_secret: str = "vigil-dev-session-secret-change-me"

    heartbeat_interval: int = 30
    suspect_seconds: int = 90
    down_seconds: int = 150
    beacon_seconds: int = 180

    auth_window_seconds: int = 300
    nonce_ttl_seconds: int = 900
    worker_interval_seconds: int = 10
    actions_enabled: bool = True
    action_cooldown_seconds: int = 300
    action_max_per_hour: int = 3
    flapping_window_seconds: int = 1800
    flapping_threshold: int = 3
    session_max_age: int = field(default=12 * 3600)
    session_https_only: bool = False

    log_dir: str = "/var/lib/vigil/logs"
    log_max_bytes: int = 10 * 1024 * 1024
    log_backups: int = 5
    log_compress: bool = True
    retention_days: int = 30
    disk_alert_percent: int = 85
    disk_alert_cooldown_seconds: int = 6 * 3600
    disk_check_interval_seconds: int = 300

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=_env_str("VIGIL_DATABASE_URL", cls.database_url),
            bootstrap_token=_env_str("VIGIL_BOOTSTRAP_TOKEN", cls.bootstrap_token),
            admin_username=_env_str("VIGIL_ADMIN_USERNAME", cls.admin_username),
            admin_key_file=_env_str("VIGIL_ADMIN_KEY_FILE", cls.admin_key_file),
            session_secret=_env_str("VIGIL_SESSION_SECRET", cls.session_secret),
            heartbeat_interval=_env_int("VIGIL_HEARTBEAT_INTERVAL", cls.heartbeat_interval),
            suspect_seconds=_env_int("VIGIL_SUSPECT_SECONDS", cls.suspect_seconds),
            down_seconds=_env_int("VIGIL_DOWN_SECONDS", cls.down_seconds),
            beacon_seconds=_env_int("VIGIL_BEACON_SECONDS", cls.beacon_seconds),
            auth_window_seconds=_env_int("VIGIL_AUTH_WINDOW_SECONDS", cls.auth_window_seconds),
            nonce_ttl_seconds=_env_int("VIGIL_NONCE_TTL_SECONDS", cls.nonce_ttl_seconds),
            worker_interval_seconds=_env_int("VIGIL_WORKER_INTERVAL", cls.worker_interval_seconds),
            actions_enabled=_env_bool("VIGIL_ACTIONS_ENABLED", cls.actions_enabled),
            action_cooldown_seconds=_env_int(
                "VIGIL_ACTION_COOLDOWN_SECONDS", cls.action_cooldown_seconds
            ),
            action_max_per_hour=_env_int(
                "VIGIL_ACTION_MAX_PER_HOUR", cls.action_max_per_hour
            ),
            flapping_window_seconds=_env_int(
                "VIGIL_FLAPPING_WINDOW_SECONDS", cls.flapping_window_seconds
            ),
            flapping_threshold=_env_int("VIGIL_FLAPPING_THRESHOLD", cls.flapping_threshold),
            session_https_only=_env_bool("VIGIL_SESSION_HTTPS_ONLY", cls.session_https_only),
            log_dir=_env_str("VIGIL_LOG_DIR", cls.log_dir),
            log_max_bytes=_env_int("VIGIL_LOG_MAX_BYTES", cls.log_max_bytes),
            log_backups=_env_int("VIGIL_LOG_BACKUPS", cls.log_backups),
            log_compress=_env_bool("VIGIL_LOG_COMPRESS", cls.log_compress),
            retention_days=_env_int("VIGIL_RETENTION_DAYS", cls.retention_days),
            disk_alert_percent=_env_int("VIGIL_DISK_ALERT_PERCENT", cls.disk_alert_percent),
            disk_alert_cooldown_seconds=_env_int(
                "VIGIL_DISK_ALERT_COOLDOWN_SECONDS", cls.disk_alert_cooldown_seconds
            ),
            disk_check_interval_seconds=_env_int(
                "VIGIL_DISK_CHECK_INTERVAL_SECONDS", cls.disk_check_interval_seconds
            ),
        )

    @property
    def thresholds(self) -> Thresholds:
        return Thresholds(
            heartbeat_interval=self.heartbeat_interval,
            suspect_seconds=self.suspect_seconds,
            down_seconds=self.down_seconds,
            beacon_seconds=self.beacon_seconds,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程级配置单例，仅读取环境变量，不产生副作用。"""
    return Settings.from_env()


def settings_from_app(request: Request) -> Settings:
    """FastAPI 依赖：返回创建应用时绑定的配置。"""
    return request.app.state.settings
