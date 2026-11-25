"""app.process：检测 procd 服务或进程是否存在。

Check 参数：
- `service`：procd 服务名（如 `dnsmasq`），优先通过 ubus 查询
- `process`：进程匹配模式，交给 `pgrep -f`；ubus 不可用时作为回退

判定：
- 服务/进程存在 → ok
- 明确不存在 → critical
- 无法判断（命令不可用、输出异常）→ unknown
"""

import json

from .base import STATUS_CRITICAL, STATUS_OK, STATUS_UNKNOWN, make_result, truncate

LAYER = "app.process"
DEPENDS_ON = ()


def _procd_status(ctx, service):
    """查询 procd 服务状态，返回 (status, error, result)。"""
    payload = json.dumps({"name": service}, ensure_ascii=False)
    result = ctx.runner.run(
        ["ubus", "call", "service", "list", payload], timeout=ctx.timeout
    )
    if result.timed_out:
        return None, "ubus 调用超时", result
    if result.error:
        return None, truncate(result.error, 200), result
    if result.exit_code != 0:
        return None, truncate(result.stderr, 200) or "ubus 退出码 %s" % result.exit_code, result

    text = result.stdout.strip()
    if not text:
        return None, "ubus 无输出", result
    try:
        data = json.loads(text)
    except ValueError:
        return None, "ubus 返回非 JSON", result
    if not isinstance(data, dict):
        return None, "ubus 返回非对象", result

    entry = data.get(service)
    if not isinstance(entry, dict):
        return {"running": False, "pids": [], "instances": 0}, "", result

    instances = entry.get("instances") or {}
    pids = []
    running = False
    if isinstance(instances, dict):
        for instance in instances.values():
            if isinstance(instance, dict) and instance.get("running") is True:
                running = True
                pid = instance.get("pid")
                if isinstance(pid, int):
                    pids.append(pid)
    return {
        "running": running,
        "pids": pids,
        "instances": len(instances) if isinstance(instances, dict) else 0,
    }, "", result


def _pgrep(ctx, pattern, extra=None):
    """通过 pgrep 判断进程是否存在，返回结果字典。"""
    result = ctx.runner.run(["pgrep", "-f", pattern], timeout=ctx.timeout)
    evidence = {"process": pattern, "source": "pgrep"}
    if extra:
        evidence.update(extra)
    if result.timed_out:
        return make_result(
            LAYER, STATUS_UNKNOWN, result.elapsed_ms, {**evidence, "reason": "pgrep 调用超时"}
        )
    if result.error:
        return make_result(
            LAYER,
            STATUS_UNKNOWN,
            result.elapsed_ms,
            {**evidence, "reason": truncate(result.error, 200)},
        )
    if result.exit_code == 0:
        pids = [int(item) for item in result.stdout.split() if item.isdigit()]
        return make_result(
            LAYER, STATUS_OK, result.elapsed_ms, {**evidence, "pids": pids}
        )
    if result.exit_code == 1:
        return make_result(
            LAYER,
            STATUS_CRITICAL,
            result.elapsed_ms,
            {**evidence, "reason": "进程不存在"},
        )
    return make_result(
        LAYER,
        STATUS_UNKNOWN,
        result.elapsed_ms,
        {
            **evidence,
            "reason": truncate(result.stderr, 200) or "pgrep 退出码 %s" % result.exit_code,
        },
    )


def detect(ctx):
    service = str(ctx.param("service") or "").strip()
    pattern = str(ctx.param("process") or "").strip()
    if not service and not pattern:
        return make_result(
            LAYER, STATUS_UNKNOWN, 0, {"reason": "缺少 service 或 process 参数"}
        )

    fallback_reason = ""
    if service:
        status, error, result = _procd_status(ctx, service)
        if status is not None:
            evidence = {"service": service, "source": "procd", **status}
            if status["running"]:
                return make_result(LAYER, STATUS_OK, result.elapsed_ms, evidence)
            return make_result(
                LAYER,
                STATUS_CRITICAL,
                result.elapsed_ms,
                {**evidence, "reason": "procd 服务未运行"},
            )
        if not pattern:
            return make_result(
                LAYER,
                STATUS_UNKNOWN,
                result.elapsed_ms if result is not None else 0,
                {"service": service, "source": "procd", "reason": error},
            )
        fallback_reason = "ubus 不可用：%s" % error

    return _pgrep(ctx, pattern, {"fallback_reason": fallback_reason} if fallback_reason else None)
