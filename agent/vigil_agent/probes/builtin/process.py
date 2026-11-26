"""process：观测 procd 服务或进程是否存在。"""

import json

from ..base import Observation, Probe, observe_error, truncate


def _procd_status(ctx, service):
    """返回 (status_dict, error)。status_dict 为 None 表示无法判断。"""
    payload = json.dumps({"name": service}, ensure_ascii=False)
    result = ctx.run(["ubus", "call", "service", "list", payload])
    if result.timed_out:
        return None, "ubus 调用超时"
    if result.error:
        return None, truncate(result.error, 200)
    if result.exit_code != 0:
        return None, truncate(result.stderr, 200) or "ubus 退出码 %s" % result.exit_code

    text = result.stdout.strip()
    if not text:
        return None, "ubus 无输出"
    try:
        data = json.loads(text)
    except ValueError:
        return None, "ubus 返回非 JSON"
    if not isinstance(data, dict):
        return None, "ubus 返回非对象"

    entry = data.get(service)
    if not isinstance(entry, dict):
        return {"running": False, "pids": [], "instances": 0}, ""

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
    }, ""


def _pgrep(ctx, pattern):
    result = ctx.run(["pgrep", "-f", pattern])
    if result.timed_out:
        return observe_error("pgrep 调用超时", result.latency_ms, {"process": pattern})
    if result.error:
        return observe_error(truncate(result.error, 200), result.latency_ms, {"process": pattern})
    if result.exit_code == 0:
        pids = [int(item) for item in result.stdout.split() if item.isdigit()]
        return Observation(
            {"process": pattern, "source": "pgrep", "running": True, "pids": pids},
            result.latency_ms,
        )
    if result.exit_code == 1:
        return Observation(
            {"process": pattern, "source": "pgrep", "running": False, "pids": []},
            result.latency_ms,
        )
    return observe_error(
        truncate(result.stderr, 200) or "pgrep 退出码 %s" % result.exit_code,
        result.latency_ms,
        {"process": pattern},
    )


class ProcessProbe(Probe):
    id = "process"
    name = "进程/服务"
    description = "查询 procd 服务状态，ubus 不可用时回退 pgrep"
    params_schema = {
        "service": {"type": "string", "required": False, "label": "procd 服务"},
        "process": {"type": "string", "required": False, "label": "进程匹配"},
    }
    default_expect = {"kind": "process", "running": True}
    default_timeout = 3.0
    remote_allowed = True

    def observe(self, ctx, params):
        service = str(params.get("service") or "").strip()
        pattern = str(params.get("process") or "").strip()
        if not service and not pattern:
            return observe_error("缺少 service 或 process 参数")

        fallback_reason = ""
        if service:
            status, error = _procd_status(ctx, service)
            if status is not None:
                return Observation(
                    {"service": service, "source": "procd", **status}, 0
                )
            if not pattern:
                return observe_error(error, 0, {"service": service, "source": "procd"})
            fallback_reason = "ubus 不可用：%s" % error

        result = _pgrep(ctx, pattern)
        if fallback_reason:
            result.data["fallback_reason"] = fallback_reason
        return result


PROBE = ProcessProbe()
