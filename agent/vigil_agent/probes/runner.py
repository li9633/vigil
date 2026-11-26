"""本地 probe 子进程执行器：stdin/stdout JSON 契约与沙箱约束。

安全约束（设计文档 3.6 / 9）：
- 固定 argv，不经过 shell；工作目录为 probe 包目录。
- 环境变量白名单，不继承服务端/客户端密钥。
- 每次执行前校验入口文件 SHA-256。
- 超时（上限 30s）与输出上限（64 KB），超限记为观测失败。
- 同一 probe 不并发执行。
"""

import hashlib
import json
import os
import sys
import threading
import time

from ..utils.text import truncate
from .base import Observation, Probe, observe_error

MAX_TIMEOUT = 30.0
MAX_STDOUT_BYTES = 64 * 1024
FAILURE_OUTPUT_LIMIT = 512


def probe_env():
    """返回执行本地 probe 的白名单环境变量。"""
    if os.name == "nt":
        # Windows 仅用于开发调试，保留 Python 启动所需的最小变量
        env = {}
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT", "COMSPEC"):
            value = os.environ.get(key)
            if value:
                env[key] = value
        return env
    return {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/tmp",
        "LC_ALL": "C",
    }


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class LocalProbe(Probe):
    """由本地 probe 包构建的 Probe 实现，执行方式为子进程。"""

    source = "local"
    remote_allowed = False

    def __init__(self, package):
        self.package = package
        self.id = package.id
        self.name = package.name or package.id
        self.description = package.description
        self.version = package.version
        self.params_schema = package.params_schema
        self.default_expect = package.default_expect
        self.default_timeout = package.timeout
        self.remote_allowed = False
        self.source = "local"
        self.directory = package.directory
        self.entry = package.entry
        self.interpreter = package.interpreter
        self.sha256 = package.sha256
        self._lock = threading.Lock()

    def argv(self):
        entry_path = os.path.join(self.directory, self.entry)
        if self.interpreter == "python3":
            # 开发环境可能没有名为 python3 的可执行文件，统一使用当前解释器
            return [sys.executable, entry_path]
        if self.interpreter in ("sh", "ash"):
            return [self.interpreter, entry_path]
        return [entry_path]

    def catalog_entry(self):
        entry = super().catalog_entry()
        entry.update(
            {
                "entry": self.entry,
                "interpreter": self.interpreter,
                "sha256": self.sha256,
            }
        )
        return entry

    def observe(self, ctx, params):
        try:
            timeout = float(ctx.timeout or self.default_timeout or 5)
        except (TypeError, ValueError):
            timeout = float(self.default_timeout or 5)
        timeout = max(0.1, min(MAX_TIMEOUT, timeout))

        if not self._lock.acquire(timeout=timeout + 1):
            return observe_error("probe 正在执行，已跳过本轮")
        try:
            return self._execute(ctx, params, timeout)
        finally:
            self._lock.release()

    def _execute(self, ctx, params, timeout):
        started = ctx.clock()
        entry_path = os.path.join(self.directory, self.entry)
        try:
            actual = file_sha256(entry_path)
        except OSError as exc:
            return observe_error("读取入口文件失败：%s" % exc)
        if actual != self.sha256:
            return observe_error("入口文件 SHA-256 校验失败，拒绝执行")

        request = {
            "check_id": str(getattr(ctx, "check_id", "") or ""),
            "params": dict(params or {}),
            "context": {
                "client_id": str(
                    ((ctx.config or {}).get("server") or {}).get("client_id") or ""
                ),
                "wan_device": str((ctx.params or {}).get("device") or ""),
                "wan_interface": str(
                    ((ctx.config or {}).get("action") or {}).get("wan_interface") or "wan"
                ),
                "now": int(time.time()),
            },
        }
        payload_text = json.dumps(request, ensure_ascii=False)

        result = ctx.run(
            self.argv(),
            timeout=timeout,
            env=probe_env(),
            cwd=self.directory,
            input_text=payload_text,
        )
        latency = result.latency_ms or int((ctx.clock() - started) * 1000)

        if result.timed_out:
            return Observation({}, latency, "probe 执行超时（%.0fs）" % timeout)
        if result.truncated:
            return Observation(
                {"stdout_head": truncate(result.stdout, 200)},
                latency,
                "probe 输出超过 %d KB" % (MAX_STDOUT_BYTES // 1024),
            )
        if result.exit_code != 0:
            detail = truncate(result.stderr or result.stdout, FAILURE_OUTPUT_LIMIT)
            return Observation(
                {"stderr": detail, "exit_code": result.exit_code},
                latency,
                "probe 退出码 %s" % result.exit_code,
            )
        if not result.stdout.strip():
            if result.stderr.strip():
                return Observation(
                    {"stderr": truncate(result.stderr, FAILURE_OUTPUT_LIMIT)},
                    latency,
                    "probe 无 stdout 输出",
                )
            return Observation({}, latency, "probe 无输出")

        try:
            payload = json.loads(result.stdout)
        except ValueError:
            return Observation(
                {"stdout_head": truncate(result.stdout, 200)},
                latency,
                "probe 输出不是合法 JSON",
            )
        if not isinstance(payload, dict):
            return Observation({}, latency, "probe 输出必须是 JSON 对象")

        data = payload.get("data")
        if not isinstance(data, dict):
            return Observation(
                {"value": data}, latency, "probe 输出的 data 必须是 JSON 对象"
            )

        reported = payload.get("latency_ms", latency)
        if isinstance(reported, bool) or not isinstance(reported, (int, float)):
            reported = latency
        error = payload.get("error")
        if error is not None and not isinstance(error, str):
            error = str(error)
        return Observation(data, int(reported), error or "")
