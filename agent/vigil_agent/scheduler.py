"""基于单调时钟的简单间隔调度器。

检测、心跳、配置拉取各自按 interval 触发。调度只依赖注入的时钟，
便于单元测试。采用「触发后按当前时间重新计算下次到期」的策略，
避免故障恢复后一次性补跑大量任务。
"""

import time


class IntervalScheduler:
    def __init__(self, clock=None):
        self.clock = clock or time.monotonic
        self._tasks = {}

    def add(self, name, interval, initial_delay=0):
        interval = float(interval)
        if interval <= 0:
            raise ValueError("interval 必须大于 0")
        now = self.clock()
        self._tasks[name] = {
            "interval": interval,
            "next_due": now + float(initial_delay),
        }
        return self

    def remove(self, name):
        self._tasks.pop(name, None)

    def update_interval(self, name, interval):
        if name in self._tasks:
            self._tasks[name]["interval"] = float(interval)
            self._tasks[name]["next_due"] = self.clock() + float(interval)

    def due(self, now=None):
        """返回本轮到期任务名，并推进下次到期时间。"""
        now = self.clock() if now is None else now
        ready = []
        for name, task in self._tasks.items():
            if now >= task["next_due"]:
                ready.append(name)
                task["next_due"] = now + task["interval"]
        return ready

    def next_delay(self, now=None):
        """距离下一个任务到期还有多少秒。"""
        if not self._tasks:
            return 1.0
        now = self.clock() if now is None else now
        return max(0.0, min(task["next_due"] - now for task in self._tasks.values()))
