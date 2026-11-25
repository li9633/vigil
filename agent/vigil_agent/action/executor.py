"""L1 白名单动作执行器。

规则（对应设计文档 3.4）：
- 只允许 `ifup_wan` 与 `restart_service` 两个动作
- `restart_service` 的服务名必须在白名单内，且做字符校验，禁止注入
- 同一动作冷却 5 分钟，每小时最多 3 次
- 冷却与计数持久化到 /overlay/vigil/state.json，重启不清零
- 全局 flock，避免 API 与自动恢复并发执行
- 动作后 20s 复检；无效则 120s 后重试一次，再复检
"""

import json
import math
import os
import re
import tempfile
import threading
import time

from ..detect.base import CommandRunner, truncate
from ..lockfile import FileLock

ACTION_IFUP_WAN = "ifup_wan"
ACTION_RESTART_SERVICE = "restart_service"

# 白名单动作定义
WHITELIST = {
    ACTION_IFUP_WAN: {"requires_target": False},
    ACTION_RESTART_SERVICE: {"requires_target": True},
}

# procd 服务名允许的字符集
SERVICE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

HOUR_SECONDS = 3600


def _empty_state():
    return {
        "schema_version": 1,
        "maintenance_until": 0,
        "actions": {},
        "updated_at": 0,
    }


def _new_result(name, target):
    return {
        "action": name,
        "target": target or "",
        "allowed": False,
        "executed": False,
        "result": "rejected",
        "reason": "",
        "message": "",
        "exit_code": None,
        "latency_ms": 0,
        "output": "",
        "cooldown_remaining": 0,
        "hourly_used": 0,
    }


class ActionExecutor:
    """执行 L1 动作并维护冷却/配额状态。"""

    def __init__(
        self,
        config,
        runner=None,
        state_path=None,
        lock_path=None,
        now=None,
        sleep=None,
        use_lock=True,
    ):
        action_cfg = config.get("action") or {}
        safety = config.get("action_safety") or {}
        storage = config.get("storage") or {}
        detect_cfg = config.get("detect") or {}

        self.config = config
        self.enabled = bool(safety.get("global_enabled", action_cfg.get("enabled", True)))
        self.max_level = str(safety.get("max_risk_level", action_cfg.get("max_level", "L1")))
        self.cooldown_seconds = int(action_cfg.get("cooldown_seconds", 300))
        self.max_per_hour = int(
            safety.get("max_actions_per_hour", action_cfg.get("max_per_hour", 3))
        )
        self.allowed_services = list(action_cfg.get("allowed_services") or [])
        self.action_timeout = int(action_cfg.get("timeout", 30))
        self.wan_interface = str(detect_cfg.get("wan_interface") or "wan")

        self.state_path = state_path or storage.get("state_path") or "/overlay/vigil/state.json"
        self.lock_path = lock_path or action_cfg.get("lock_path") or "/var/run/vigil/action.lock"
        self.runner = runner or CommandRunner(default_timeout=self.action_timeout)
        self.now = now or time.time
        self._sleep = sleep or time.sleep
        self.use_lock = use_lock
        self._state_lock = threading.RLock()

    # ---------- 状态文件 ----------

    def _load_state(self):
        try:
            with open(self.state_path, encoding="utf-8") as handle:
                state = json.load(handle)
        except (OSError, ValueError):
            return _empty_state()
        if not isinstance(state, dict):
            return _empty_state()
        base = _empty_state()
        base["maintenance_until"] = _as_number(state.get("maintenance_until"), 0)
        actions = state.get("actions")
        if isinstance(actions, dict):
            cleaned = {}
            for key, value in actions.items():
                if not isinstance(value, dict):
                    continue
                recent = [
                    float(item)
                    for item in value.get("recent") or []
                    if isinstance(item, (int, float)) and not isinstance(item, bool)
                ]
                cleaned[key] = {
                    "last_run": _as_number(value.get("last_run"), 0),
                    "recent": recent,
                    "last_result": str(value.get("last_result") or ""),
                }
            base["actions"] = cleaned
        return base

    def _save_state(self, state):
        directory = os.path.dirname(self.state_path) or "."
        os.makedirs(directory, exist_ok=True)
        state["updated_at"] = int(self.now())
        fd, tmp_path = tempfile.mkstemp(prefix=".vigil-state-", dir=directory, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(tmp_path, self.state_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        try:
            os.chmod(self.state_path, 0o600)
        except OSError:
            pass

    # ---------- 内部工具 ----------

    @staticmethod
    def _record_key(name, target):
        if name == ACTION_RESTART_SERVICE and target:
            return "%s:%s" % (name, target)
        return name

    def _build_command(self, name, target):
        if name == ACTION_IFUP_WAN:
            return ["ifup", self.wan_interface]
        return ["/etc/init.d/%s" % target, "restart"]

    def _validate(self, name, target):
        """返回 (reason, message)，空字符串表示通过。"""
        if not self.enabled:
            return "disabled", "配置已禁用自动动作"
        if self.max_level != "L1":
            return "level_disabled", "当前动作等级已限制为 %s" % self.max_level
        if name not in WHITELIST:
            return "not_whitelisted", "动作不在白名单内：%s" % name
        if WHITELIST[name]["requires_target"]:
            if not target:
                return "target_required", "restart_service 需要指定服务名"
            if not SERVICE_NAME_RE.match(target):
                return "target_invalid", "服务名包含非法字符"
            if target not in self.allowed_services:
                return "target_not_whitelisted", "服务不在白名单内：%s" % target
        return "", ""

    # ---------- 执行 ----------

    def execute(self, name, target=None, force=False):
        """执行一次动作；force=True 时忽略冷却（用于动作后重试）。"""
        result = _new_result(name, target)
        reason, message = self._validate(name, target)
        if reason:
            result["reason"] = reason
            result["message"] = message
            return result

        lock = FileLock(self.lock_path) if self.use_lock else None
        if lock is not None and not lock.acquire():
            result["reason"] = "locked"
            result["message"] = "另一个动作正在执行"
            return result

        try:
            with self._state_lock:
                now = float(self.now())
                state = self._load_state()

                if float(state.get("maintenance_until") or 0) > now:
                    result["reason"] = "maintenance"
                    result["message"] = "处于维护窗口，禁止执行动作"
                    result["cooldown_remaining"] = int(
                        math.ceil(float(state["maintenance_until"]) - now)
                    )
                    return result

                key = self._record_key(name, target)
                record = state["actions"].get(key) or {"last_run": 0, "recent": []}
                recent = [
                    float(item) for item in record.get("recent", []) if now - float(item) < HOUR_SECONDS
                ]
                result["hourly_used"] = len(recent)

                if len(recent) >= self.max_per_hour:
                    result["reason"] = "hourly_limit"
                    result["message"] = "每小时动作次数已达上限（%d 次）" % self.max_per_hour
                    return result

                last_run = float(record.get("last_run") or 0)
                remaining = last_run + self.cooldown_seconds - now
                if not force and remaining > 0:
                    result["reason"] = "cooldown"
                    result["message"] = "动作冷却中"
                    result["cooldown_remaining"] = int(math.ceil(remaining))
                    return result

                command = self._build_command(name, target)
                result["allowed"] = True
                result["executed"] = True
                result["command"] = " ".join(command)

                command_result = self.runner.run(command, timeout=self.action_timeout)
                result["exit_code"] = command_result.exit_code
                result["latency_ms"] = command_result.elapsed_ms
                output = (command_result.stderr or command_result.stdout or "").strip()
                if not output and command_result.error:
                    output = command_result.error
                result["output"] = truncate(output, 2048)
                if command_result.timed_out:
                    result["result"] = "failed"
                    result["message"] = "动作执行超时"
                elif command_result.exit_code == 0 and not command_result.error:
                    result["result"] = "success"
                else:
                    result["result"] = "failed"
                    result["message"] = "动作执行失败（退出码 %s）" % command_result.exit_code

                recent.append(now)
                record["last_run"] = now
                record["recent"] = recent
                record["last_result"] = result["result"]
                state["actions"][key] = record
                self._save_state(state)
                result["hourly_used"] = len(recent)
                return result
        finally:
            if lock is not None:
                lock.release()

    def execute_with_recheck(
        self,
        name,
        recheck,
        target=None,
        recheck_delay=20,
        retry_delay=120,
    ):
        """动作 → 20s 复检 → 无效则 120s 后重试 → 再复检。

        recheck 为无参可调用对象，返回 True 表示已恢复。
        返回值 {"attempts": [...], "recovered": bool}。
        """
        attempts = []
        first = self.execute(name, target)
        attempts.append(first)
        if not first["executed"]:
            return {"attempts": attempts, "recovered": False}

        self._sleep(recheck_delay)
        if recheck():
            return {"attempts": attempts, "recovered": True}

        self._sleep(retry_delay)
        second = self.execute(name, target, force=True)
        attempts.append(second)
        if not second["executed"]:
            return {"attempts": attempts, "recovered": False}

        self._sleep(recheck_delay)
        return {"attempts": attempts, "recovered": bool(recheck())}

    # ---------- 辅助接口 ----------

    def set_maintenance(self, seconds):
        """设置维护窗口，期间禁止动作。"""
        with self._state_lock:
            state = self._load_state()
            state["maintenance_until"] = float(self.now()) + max(0, float(seconds))
            self._save_state(state)
            return state["maintenance_until"]

    def apply_safety(self, safety):
        """热更新全局动作安全上限。"""
        safety = safety or {}
        if "global_enabled" in safety:
            self.enabled = bool(safety["global_enabled"])
        if "max_risk_level" in safety:
            self.max_level = str(safety["max_risk_level"])
        if "max_actions_per_hour" in safety:
            self.max_per_hour = int(safety["max_actions_per_hour"])

    def stats(self):
        with self._state_lock:
            now = float(self.now())
            state = self._load_state()
            actions = {}
            for key, record in state.get("actions", {}).items():
                recent = [
                    float(item)
                    for item in record.get("recent", [])
                    if now - float(item) < HOUR_SECONDS
                ]
                actions[key] = {
                    "last_run": record.get("last_run", 0),
                    "hourly_used": len(recent),
                    "cooldown_remaining": max(
                        0,
                        int(
                            math.ceil(
                                float(record.get("last_run", 0))
                                + self.cooldown_seconds
                                - now
                            )
                        ),
                    ),
                    "last_result": record.get("last_result", ""),
                }
            return {
                "enabled": self.enabled,
                "max_level": self.max_level,
                "cooldown_seconds": self.cooldown_seconds,
                "max_per_hour": self.max_per_hour,
                "allowed_services": list(self.allowed_services),
                "maintenance_until": state.get("maintenance_until", 0),
                "actions": actions,
            }


def _as_number(value, default):
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default
