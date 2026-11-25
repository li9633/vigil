"""恢复链执行器：按 Guard 的 recovery 顺序执行动作并逐步验证。"""

import time

from .fsm import GuardStateMachine


class RecoveryRunner:
    """执行 Guard 的恢复动作链。

    executor: ActionExecutor，提供 execute(action, target)
    fsm: GuardStateMachine，用于标记 RECOVERING / 结束状态
    verify: 无参可调用对象，返回 True 表示当前 Guard 已恢复
    """

    def __init__(self, executor, fsm, verify, sleep=None, on_action=None):
        self.executor = executor
        self.fsm = fsm
        self.verify = verify
        self._sleep = sleep or time.sleep
        self.on_action = on_action

    def run(self, guard):
        guard_id = str(guard.get("id"))
        self.fsm.start_recovery(guard_id)
        attempts = []
        executed_any = False

        for step in guard.get("recovery") or []:
            action = step.get("action")
            target = step.get("target")
            result = self.executor.execute(action, target)
            result["guard_id"] = guard_id
            attempts.append(result)
            if self.on_action:
                self.on_action(guard, result)

            if not result.get("executed"):
                if step.get("continue_on_failure"):
                    continue
                break
            executed_any = True

            delay = int(step.get("verify_after", 20) or 0)
            if delay:
                self._sleep(delay)

            if self.verify():
                state = self.fsm.finish_recovery(guard, True)
                return {"recovered": True, "attempts": attempts, "state": state}

            if not step.get("continue_on_failure"):
                break

        if not executed_any:
            # 动作被冷却/维护/白名单拒绝，不视为一次失败的恢复轮次
            state = self.fsm.abort_recovery(guard_id)
            return {
                "recovered": False,
                "attempts": attempts,
                "state": state,
                "skipped": True,
            }

        state = self.fsm.finish_recovery(guard, False)
        return {"recovered": False, "attempts": attempts, "state": state}
