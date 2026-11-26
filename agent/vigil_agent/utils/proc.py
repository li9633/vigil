"""子进程执行工具：固定参数列表、超时强杀、输出截断。"""

import os
import signal
import subprocess
import time
from dataclasses import dataclass

DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_OUTPUT = 64 * 1024


@dataclass
class ProcessResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    truncated: bool = False
    latency_ms: int = 0
    error: str = ""


def _kill_process(process):
    """超时后强杀进程；POSIX 杀整个进程组，避免子进程残留。"""
    try:
        if os.name == "nt":
            process.kill()
        else:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except OSError:
                process.kill()
    except OSError:
        pass


def _truncate(text, limit):
    if text is None:
        return "", False
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def run_command(
    args,
    timeout=DEFAULT_TIMEOUT,
    max_output=DEFAULT_MAX_OUTPUT,
    cwd=None,
    env=None,
    input_text=None,
    inherit_env=True,
):
    """执行固定 argv。

    - 禁止传入字符串，避免 shell 拼接。
    - 超时返回 timed_out=True，不抛异常。
    - stdout/stderr 超过 max_output 时截断并标记 truncated。
    """
    if isinstance(args, str):
        raise TypeError("命令必须是参数列表，禁止拼接 shell 字符串")
    argv = list(args)
    if not argv or not all(isinstance(item, str) for item in argv):
        raise ValueError("命令必须是非空的字符串数组")
    if timeout is None or float(timeout) <= 0:
        raise ValueError("timeout 必须大于 0")
    if max_output is None or int(max_output) <= 0:
        raise ValueError("max_output 必须大于 0")

    merged_env = dict(os.environ) if inherit_env else {}
    if env:
        merged_env.update({str(key): str(value) for key, value in env.items()})

    popen_kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "encoding": "utf-8",
        "errors": "replace",
        "cwd": cwd,
        "env": merged_env,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True

    started = time.monotonic()
    try:
        process = subprocess.Popen(argv, **popen_kwargs)
    except OSError as exc:
        return ProcessResult(
            exit_code=-1,
            latency_ms=int((time.monotonic() - started) * 1000),
            error="命令启动失败：%s" % exc,
        )

    timed_out = False
    try:
        stdout, stderr = process.communicate(input=input_text, timeout=float(timeout))
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process(process)
        try:
            stdout, stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""

    stdout, stdout_truncated = _truncate(stdout, int(max_output))
    stderr, stderr_truncated = _truncate(stderr, int(max_output))
    exit_code = process.returncode if process.returncode is not None else -1
    if timed_out:
        exit_code = -int(getattr(signal, "SIGKILL", 9))
    return ProcessResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        truncated=stdout_truncated or stderr_truncated,
        latency_ms=int((time.monotonic() - started) * 1000),
    )
