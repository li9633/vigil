"""检测基础设施：命令执行器、上下文、注册表与状态聚合。

检测模块统一输出 `{layer, status, latency_ms, evidence}`。
状态取值：ok / warn / critical / unknown / skipped。
单次异常不会直接判为 critical：同一层连续 3 次异常后才对外呈现 critical，
聚合状态只保存在内存，进程重启清零（动作冷却状态由 action 模块单独持久化）。
"""

import json
import os
import signal
import subprocess
import time

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_CRITICAL = "critical"
STATUS_UNKNOWN = "unknown"
STATUS_SKIPPED = "skipped"

# 视为「异常」的原始状态
ABNORMAL_STATUSES = (STATUS_WARN, STATUS_CRITICAL, STATUS_UNKNOWN)

# 外部命令输出的最大保留长度（写入证据时再截断）
STDERR_LIMIT = 4096


def truncate(text, limit=STDERR_LIMIT):
    """截断长文本，避免证据体积失控。"""
    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + "…(截断)"


def make_result(layer, status, latency_ms=0, evidence=None):
    """构造统一的检测结果。"""
    return {
        "layer": layer,
        "status": status,
        "latency_ms": int(latency_ms),
        "evidence": evidence or {},
    }


class CommandResult:
    """外部命令执行结果。"""

    __slots__ = ("exit_code", "stdout", "stderr", "elapsed_ms", "timed_out", "error")

    def __init__(
        self,
        exit_code,
        stdout="",
        stderr="",
        elapsed_ms=0,
        timed_out=False,
        error="",
    ):
        self.exit_code = exit_code
        self.stdout = stdout or ""
        self.stderr = stderr or ""
        self.elapsed_ms = int(elapsed_ms)
        self.timed_out = timed_out
        self.error = error or ""

    def __repr__(self):
        return "CommandResult(exit_code=%r, timed_out=%r, error=%r)" % (
            self.exit_code,
            self.timed_out,
            self.error,
        )


def _kill_process(process):
    """超时后强杀进程；POSIX 杀整个进程组，避免 shell 子进程残留。"""
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


class CommandRunner:
    """统一的外部命令执行器：固定超时 + 强杀兜底。

    测试中可注入替身对象，只需实现 run(args, timeout) 并返回 CommandResult。
    """

    def __init__(self, default_timeout=10):
        self.default_timeout = default_timeout

    def run(self, args, timeout=None, env=None):
        if isinstance(args, str):
            raise TypeError("命令必须是参数列表，禁止拼接 shell 字符串")
        timeout = self.default_timeout if timeout is None else timeout
        popen_kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "encoding": "utf-8",
            "errors": "replace",
        }
        if env:
            merged_env = dict(os.environ)
            merged_env.update({str(key): str(value) for key, value in env.items()})
            popen_kwargs["env"] = merged_env
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        else:
            popen_kwargs["start_new_session"] = True

        started = time.monotonic()
        try:
            process = subprocess.Popen(list(args), **popen_kwargs)
        except OSError as exc:
            return CommandResult(
                exit_code=-1,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                error="命令启动失败：%s" % exc,
            )

        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process(process)
            try:
                stdout, stderr = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
        elapsed_ms = int((time.monotonic() - started) * 1000)
        exit_code = process.returncode if process.returncode is not None else -1
        if timed_out:
            exit_code = -int(getattr(signal, "SIGKILL", 9))
        return CommandResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            elapsed_ms=elapsed_ms,
            timed_out=timed_out,
        )


class DetectContext:
    """一次检测周期共享的上下文。

    - config: 完整配置
    - runner: 命令执行器（可注入）
    - sys_class_net: carrier 文件根目录（测试可指向临时目录）
    - http_get: HTTP 探测函数 (url, timeout) -> (status_code, latency_ms, error)
    - cache: 周期内缓存（例如 ubus 状态只取一次）
    """

    def __init__(
        self,
        config,
        runner=None,
        sys_class_net="/sys/class/net",
        http_get=None,
        clock=None,
        params=None,
        cache=None,
    ):
        self.config = config
        self.detect = config.get("detect", {}) if isinstance(config, dict) else {}
        self.runner = runner or CommandRunner()
        self.sys_class_net = sys_class_net
        self.http_get = http_get
        self.clock = clock or time.monotonic
        self.params = params or {}
        self.cache = cache if cache is not None else {}

    def param(self, key, default=None):
        """读取 Check 级参数，未设置时回退到默认值。"""
        if key in self.params and self.params[key] is not None:
            return self.params[key]
        return default

    @property
    def timeout(self):
        value = self.params.get("timeout", self.detect.get("timeout", 3))
        try:
            return float(value)
        except (TypeError, ValueError):
            return 3.0

    @property
    def wan_interface(self):
        value = self.param("interface", self.detect.get("wan_interface") or "wan")
        return str(value)


def ubus_wan_status(ctx):
    """读取 `ubus call network.interface.<wan> status`。

    返回 (data, error, raw_result)。data 为 None 表示不可用。
    结果在一次检测周期内缓存，pppoe / route / carrier 共享。
    """
    key = "ubus.wan_status"
    if key in ctx.cache:
        return ctx.cache[key]

    target = "network.interface.%s" % ctx.wan_interface
    result = ctx.runner.run(["ubus", "call", target, "status"], timeout=ctx.timeout)
    data = None
    error = ""
    if result.timed_out:
        error = "ubus 调用超时"
    elif result.error:
        error = truncate(result.error, 512)
    elif result.exit_code != 0:
        error = truncate(result.stderr, 512) or ("ubus 退出码 %s" % result.exit_code)
    elif result.stdout.strip():
        try:
            parsed = json.loads(result.stdout)
            if isinstance(parsed, dict):
                data = parsed
            else:
                error = "ubus 返回非对象"
        except ValueError:
            error = "ubus 返回非 JSON"
    else:
        error = "ubus 无输出"

    ctx.cache[key] = (data, error, result)
    return data, error, result


def resolve_wan_device(ctx):
    """解析 WAN 物理设备名。

    显式配置优先；`auto` 时顺序为：
    `uci get network.wan.device` → ubus status 的 l3_device/device → 解析失败。
    返回 (device, source)，device 为 None 表示无法解析。
    """
    key = "wan.device"
    if key in ctx.cache:
        return ctx.cache[key]

    configured = str(
        ctx.param("device", ctx.detect.get("wan_device") or "auto")
    ).strip()
    if configured and configured != "auto":
        ctx.cache[key] = (configured, "config")
        return ctx.cache[key]

    result = ctx.runner.run(["uci", "get", "network.wan.device"], timeout=ctx.timeout)
    if result.exit_code == 0:
        device = result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else ""
        if device and device != "auto":
            ctx.cache[key] = (device, "uci")
            return ctx.cache[key]

    data, _error, _raw = ubus_wan_status(ctx)
    if data:
        for field in ("l3_device", "device"):
            device = data.get(field)
            if isinstance(device, str) and device.strip():
                ctx.cache[key] = (device.strip(), "ubus")
                return ctx.cache[key]

    ctx.cache[key] = (None, "unresolved")
    return ctx.cache[key]


class DetectorRegistry:
    """检测器注册表，按依赖顺序拓扑排序。"""

    def __init__(self):
        self._detectors = {}

    def register(self, layer, func, depends_on=()):
        if layer in self._detectors:
            raise ValueError("检测层重复注册：%s" % layer)
        if not callable(func):
            raise TypeError("检测器必须可调用：%s" % layer)
        self._detectors[layer] = {
            "func": func,
            "depends_on": tuple(depends_on),
        }
        return self

    def layers(self):
        return list(self._detectors.keys())

    def depends_on(self, layer):
        return self._detectors[layer]["depends_on"]

    def func(self, layer):
        return self._detectors[layer]["func"]

    def ancestors(self, layer):
        """返回全部上游层（去重，按发现顺序）。"""
        seen = []
        pending = list(self._detectors[layer]["depends_on"])
        while pending:
            dep = pending.pop(0)
            if dep in seen:
                continue
            seen.append(dep)
            if dep in self._detectors:
                pending.extend(self._detectors[dep]["depends_on"])
        return seen

    def ordered(self):
        order = []
        state = {}

        def visit(name):
            if name not in self._detectors:
                raise ValueError("依赖的检测层未注册：%s" % name)
            mark = state.get(name, 0)
            if mark == 1:
                raise ValueError("检测层存在循环依赖：%s" % name)
            if mark == 2:
                return
            state[name] = 1
            for dep in self._detectors[name]["depends_on"]:
                visit(dep)
            state[name] = 2
            order.append(name)

        for layer in self._detectors:
            visit(layer)
        return order


class DetectionEngine:
    """执行检测、维护连续异常计数并聚合对外状态。"""

    def __init__(self, registry=None, failure_threshold=3):
        self.registry = registry if registry is not None else default_registry()
        self.failure_threshold = max(1, int(failure_threshold))
        self._streaks = {}
        self._last = {}

    def reset(self):
        self._streaks.clear()
        self._last.clear()

    def streaks(self):
        return dict(self._streaks)

    def _normalize(self, layer, raw):
        if not isinstance(raw, dict) or "status" not in raw:
            raise TypeError("检测器返回值缺少 status 字段：%s" % layer)
        status = str(raw.get("status"))
        try:
            latency = int(raw.get("latency_ms") or 0)
        except (TypeError, ValueError):
            latency = 0
        evidence = raw.get("evidence")
        if not isinstance(evidence, dict):
            evidence = {"value": evidence} if evidence is not None else {}
        return {
            "layer": layer,
            "status": status,
            "latency_ms": latency,
            "evidence": evidence,
        }

    def _aggregate(self, layer, result):
        raw_status = result["status"]
        if raw_status == STATUS_SKIPPED:
            return result
        if raw_status == STATUS_OK:
            self._streaks[layer] = 0
            return result

        streak = self._streaks.get(layer, 0) + 1
        self._streaks[layer] = streak
        evidence = dict(result["evidence"])
        evidence["raw_status"] = raw_status
        evidence["consecutive_failures"] = streak
        evidence["threshold"] = self.failure_threshold
        status = STATUS_CRITICAL if streak >= self.failure_threshold else STATUS_WARN
        return {
            "layer": layer,
            "status": status,
            "latency_ms": result["latency_ms"],
            "evidence": evidence,
        }

    def run(self, ctx):
        """按依赖顺序执行一轮检测，返回 {layer: aggregated_result}。"""
        results = {}
        for layer in self.registry.ordered():
            blocked_by = None
            for ancestor in self.registry.ancestors(layer):
                ancestor_status = results.get(ancestor, {}).get("status")
                if ancestor_status in (STATUS_CRITICAL, STATUS_SKIPPED):
                    blocked_by = ancestor
                    break

            if blocked_by:
                result = make_result(
                    layer,
                    STATUS_SKIPPED,
                    0,
                    {
                        "reason": "上游 %s 异常，跳过本层检测" % blocked_by,
                        "blocked_by": blocked_by,
                    },
                )
            else:
                started = ctx.clock()
                try:
                    raw = self.registry.func(layer)(ctx)
                    result = self._normalize(layer, raw)
                except Exception as exc:  # 检测器自身异常不应拖垮整个循环
                    result = make_result(
                        layer,
                        STATUS_UNKNOWN,
                        int((ctx.clock() - started) * 1000),
                        {
                            "error": truncate("%s: %s" % (type(exc).__name__, exc), 512),
                        },
                    )
                result = self._aggregate(layer, result)

            results[layer] = result
            self._last[layer] = result
        return results


def default_registry():
    """构建内置检测器注册表。延迟导入避免循环依赖。"""
    from . import carrier, dns, http, pppoe, route

    registry = DetectorRegistry()
    for module in (carrier, pppoe, route, dns, http):
        registry.register(module.LAYER, module.detect, module.DEPENDS_ON)
    return registry
