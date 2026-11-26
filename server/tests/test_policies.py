"""运行时策略配置测试。"""

from __future__ import annotations

import pytest

from app.db import session_scope
from app.policies import (
    default_policies,
    effective_settings,
    load_policies,
    save_policies,
)
from app.settings import Settings


def test_policy_defaults_and_roundtrip(db_ready: None, settings: Settings) -> None:
    with session_scope() as db:
        defaults = default_policies(settings)
        assert load_policies(db, settings) == defaults

        saved = save_policies(
            db,
            {"flapping_threshold": 5, "actions_enabled": False},
            settings,
        )
        assert saved["flapping_threshold"] == 5
        assert saved["actions_enabled"] is False
        assert saved["heartbeat_interval"] == settings.heartbeat_interval

    with session_scope() as db:
        effective = effective_settings(db, settings)
        assert effective.flapping_threshold == 5
        assert effective.actions_enabled is False
        assert effective.down_seconds == settings.down_seconds
        assert effective.action_cooldown_seconds == settings.action_cooldown_seconds


@pytest.mark.parametrize(
    "payload",
    [
        {"flapping_threshold": 1},
        {"disk_alert_percent": 100},
        {"suspect_seconds": 60, "down_seconds": 60},
        {"heartbeat_interval": 200, "suspect_seconds": 150},
        {"actions_enabled": "maybe"},
    ],
)
def test_invalid_policies_rejected(
    db_ready: None, settings: Settings, payload: dict[str, object]
) -> None:
    with session_scope() as db:
        with pytest.raises(ValueError):
            save_policies(db, payload, settings)
