"""command：执行固定 argv（本地专用，禁止服务端配置引用）。"""

from ..base import Observation, Probe, observe_error, truncate

MAX_OUTPUT = 4096


class CommandProbe(Probe):
    id = "command"
    name = "本地命令"
    description = "执行固定 argv 并按退出码 / 输出判定（仅本地可配置）"
    params_schema = {
        "argv": {
            "type": "array",
            "items": "string",
            "required": True,
            "label": "命令参数",
        },
        "cwd": {"type": "string", "required": False, "label": "工作目录"},
    }
    default_expect = {"kind": "command", "exit_code": 0}
    default_timeout = 10.0
    remote_allowed = False

    def observe(self, ctx, params):
        argv = params.get("argv")
        if not isinstance(argv, list) or not argv:
            return observe_error("缺少 argv 参数")
        cwd = str(params.get("cwd") or "").strip() or None

        result = ctx.run(argv, cwd=cwd)
        data = {
            "argv": [str(item) for item in argv],
            "exit_code": result.exit_code,
            "stdout": truncate(result.stdout, MAX_OUTPUT),
            "stderr": truncate(result.stderr, MAX_OUTPUT),
            "timed_out": bool(result.timed_out),
        }
        if result.error:
            return observe_error(result.error, result.latency_ms, data)
        return Observation(data, result.latency_ms)


PROBE = CommandProbe()
