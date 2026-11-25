"""Guard 状态机：失败计数、恢复确认、放弃与退避。

检测结果由 guards.checks 提供，动作执行由调用方负责；本模块只维护每个 Guard 的
状态与计数，并把状态持久化到 state 文件，保证重启后冷却与退避不被清零。
"""

import copy
import json
import os
import tempfile
import threading
import time

from .checks import STATUS_SKIPPED

STATE_OK = "OK"
STATE_PENDING = "PENDING"
STATE_FAILED = "FAILED"
STATE_RECOVERING = "RECOVERING"
STATE_GAVE_UP = "GAVE_UP"

# 放弃后的指数退避（秒）：5m → 15m → 60m，之后维持 60m
BACKOFF_SECONDS = (300, 900, 3600)


def _new_state():
    return {
        "status": STATE_OK,
        "failure_count": 0,
        "success_count": 0,
        "failures": [],
        "recovery_rounds": 0,
        "rearm_at": 0,
        "updated_at": 0,
        "last_recovery_id": "",
    }


class GuardStateMachine:
    """按 Guard 维护状态；同一 Guard 的检测串行执行。"""

    def __init__(self, state_path=None, now=None, persist=True):
        self.state_path = state_path
        self.now = now or time.time
        self.persist = bool(persist and state_path)
        self._lock = threading.RLock()
        self._states = self._load()

    # ---------- 持久化 ----------

    def _load(self):
        if not self.persist:
            return {}
        try:
            with open(self.state_path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return {}
        guards = data.get("guards") if isinstance(data, dict) else None
        if not isinstance(guards, dict):
            return {}
        states = {}
        for guard_id, raw in guards.items():
            if not isinstance(raw, dict):
                continue
            state = _new_state()
            state["status"] = str(raw.get("status") or STATE_OK)
            if state["status"] not in (
                STATE_OK,
                STATE_PENDING,
                STATE_FAILED,
                STATE_RECOVERING,
                STATE_GAVE_UP,
            ):
                state["status"] = STATE_OK
            for key in ("failure_count", "success_count", "recovery_rounds"):
                value = raw.get(key, 0)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    state[key] = value
            for key in ("rearm_at", "updated_at"):
                value = raw.get(key, 0)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    state[key] = float(value)
            failures = raw.get("failures")
            if isinstance(failures, list):
                state["failures"] = [
                    float(item)
                    for item in failures
                    if isinstance(item, (int, float)) and not isinstance(item, bool)
                ]
            recovery_id = raw.get("last_recovery_id")
            if isinstance(recovery_id, str):
                state["last_recovery_id"] = recovery_id
            states[str(guard_id)] = state
        return states

    def _save(self):
        if not self.persist:
            return
        directory = os.path.dirname(self.state_path) or "."
        os.makedirs(directory, exist_ok=True)
        payload = {"schema_version": 1, "guards": self._states}
        fd, tmp_path = tempfile.mkstemp(prefix=".vigil-guards-", dir=directory, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
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

    # ---------- 状态查询 ----------

    def snapshot(self, guard_id):
        with self._lock:
            state = self._states.get(str(guard_id))
            return copy.deepcopy(state) if state else _new_state()

    def all_states(self):
        with self._lock:
            return copy.deepcopy(self._states)

    # ---------- 状态推进 ----------

    def process(self, guard, check_status, now=None):
        """输入一轮聚合后的 Check 状态，推进状态机并返回快照。"""
        guard_id = str(guard.get("id"))
        policy = guard.get("policy") or {}
        now = float(now if now is not None else self.now())

        with self._lock:
            state = self._states.setdefault(guard_id, _new_state())
            state["updated_at"] = now

            if check_status == STATUS_SKIPPED:
                # 上游故障导致本 Guard 无法判断，不改变自身计数
                self._save()
                return copy.deepcopy(state)

            if state["status"] == STATE_RECOVERING:
                # 恢复流程进行中，由 finish_recovery 决定结果
                self._save()
                return copy.deepcopy(state)

            if check_status == "ok":
                state["success_count"] += 1
                state["failure_count"] = 0
                state["failures"] = []
                success_threshold = int(policy.get("success_threshold", 1) or 1)
                if state["status"] in (
                    STATE_FAILED,
                    STATE_PENDING,
                    STATE_GAVE_UP,
                    STATE_RECOVERING,
                ) and state["success_count"] >= success_threshold:
                    state["status"] = STATE_OK
                    state["recovery_rounds"] = 0
                    state["rearm_at"] = 0
                elif state["status"] == STATE_OK:
                    state["recovery_rounds"] = 0
                    state["rearm_at"] = 0
            else:
                state["success_count"] = 0
                if state["status"] == STATE_GAVE_UP and now < float(state["rearm_at"]):
                    self._save()
                    return copy.deepcopy(state)

                if state["status"] == STATE_GAVE_UP:
                    # 退避结束，重新武装
                    state["status"] = STATE_PENDING
                    state["failure_count"] = 0
                    state["failures"] = []
                    state["rearm_at"] = 0

                state["failure_count"] += 1
                state["failures"].append(now)
                threshold = int(policy.get("fail_threshold", 2) or 2)
                mode = policy.get("fail_mode", "consecutive")

                if mode == "window":
                    window = float(policy.get("fail_window", 90) or 90)
                    state["failures"] = [
                        item for item in state["failures"] if now - item <= window
                    ]
                    failures_in_window = len(state["failures"])
                else:
                    state["failures"] = []
                    failures_in_window = state["failure_count"]

                if failures_in_window >= threshold:
                    state["status"] = STATE_FAILED
                else:
                    state["status"] = STATE_PENDING

            self._save()
            return copy.deepcopy(state)

    def should_recover(self, guard_id, now=None):
        now = float(now if now is not None else self.now())
        with self._lock:
            state = self._states.get(str(guard_id))
            if not state or state["status"] != STATE_FAILED:
                return False
            return now >= float(state.get("rearm_at") or 0)

    def start_recovery(self, guard_id, now=None, recovery_id=None):
        with self._lock:
            state = self._states.setdefault(str(guard_id), _new_state())
            state["status"] = STATE_RECOVERING
            state["updated_at"] = float(now if now is not None else self.now())
            if recovery_id:
                state["last_recovery_id"] = str(recovery_id)
            self._save()
            return copy.deepcopy(state)

    def abort_recovery(self, guard_id, now=None):
        """动作因冷却/维护被拒绝，未真正执行：回到 FAILED，不计入放弃轮次。"""
        with self._lock:
            state = self._states.setdefault(str(guard_id), _new_state())
            state["status"] = STATE_FAILED
            state["updated_at"] = float(now if now is not None else self.now())
            self._save()
            return copy.deepcopy(state)

    def finish_recovery(self, guard, recovered, now=None):
        """动作链结束后调用；recovered 表示验证是否通过。"""
        guard_id = str(guard.get("id"))
        policy = guard.get("policy") or {}
        now = float(now if now is not None else self.now())

        with self._lock:
            state = self._states.setdefault(guard_id, _new_state())
            state["updated_at"] = now
            if recovered:
                state["status"] = STATE_OK
                state["failure_count"] = 0
                state["failures"] = []
                state["success_count"] = int(policy.get("success_threshold", 1) or 1)
                state["recovery_rounds"] = 0
                state["rearm_at"] = 0
            else:
                state["success_count"] = 0
                state["recovery_rounds"] += 1
                max_rounds = int(policy.get("gave_up_after", 3) or 3)
                if state["recovery_rounds"] >= max_rounds:
                    index = min(state["recovery_rounds"] - max_rounds, len(BACKOFF_SECONDS) - 1)
                    state["status"] = STATE_GAVE_UP
                    state["rearm_at"] = now + BACKOFF_SECONDS[index]
                else:
                    state["status"] = STATE_FAILED
            self._save()
            return copy.deepcopy(state)
