"""M8-7 压测与故障演练：运行场景并生成 Markdown 报告。

用法：
    python scripts/drill.py
    python scripts/drill.py --report dist/m8-7-report.md
"""

from __future__ import annotations

import argparse
import platform
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Drill:
    name: str
    description: str
    command: list[str]
    cwd: Path
    timeout: int = 300


DRILLS = [
    Drill(
        name="服务端高频心跳与事件突发",
        description="120 次心跳 + 1000 条事件，校验全量落库、重复批次幂等与耗时",
        command=["uv", "run", "pytest", "-q", "-s", "tests/test_load_drill.py"],
        cwd=ROOT / "server",
    ),
    Drill(
        name="客户端断网、重启与磁盘满",
        description="断网累积、恢复补传、spool 重启续传、磁盘满与重写失败不崩溃",
        command=["uv", "run", "python", "-m", "unittest", "tests.test_fault_drills", "-v"],
        cwd=ROOT / "agent",
    ),
    Drill(
        name="通知失败重试与死信",
        description="通知渠道失败后重试 3 次，耗尽转死信并通过剩余渠道提醒",
        command=[
            "uv",
            "run",
            "pytest",
            "-q",
            "tests/test_notifications.py",
            "-k",
            "retry or dead",
        ],
        cwd=ROOT / "server",
    ),
]


def _tail(text: str, limit: int = 6000) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else "...\n" + text[-limit:]


def _run(drill: Drill) -> tuple[bool, float, str]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            drill.command,
            cwd=str(drill.cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=drill.timeout,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        return completed.returncode == 0, time.perf_counter() - started, output
    except FileNotFoundError as exc:
        return False, time.perf_counter() - started, str(exc)
    except subprocess.TimeoutExpired:
        return False, time.perf_counter() - started, "场景执行超时"


def _metric(output: str) -> str:
    match = re.search(r"M8-7 load drill: ([^\r\n]+)", output)
    return match.group(1) if match else ""


def main() -> int:
    parser = argparse.ArgumentParser(description="M8-7 压测与故障演练")
    parser.add_argument(
        "--report",
        default=str(ROOT / "dist" / "m8-7-report.md"),
        help="Markdown 报告输出路径",
    )
    args = parser.parse_args()

    results = []
    for drill in DRILLS:
        print("==> %s" % drill.name, flush=True)
        ok, elapsed, output = _run(drill)
        results.append((drill, ok, elapsed, output))
        print("    %s %.2fs" % ("PASS" if ok else "FAIL", elapsed), flush=True)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# M8-7 压测与故障演练报告",
        "",
        "- 生成时间：%s" % now,
        "- 运行环境：%s / Python %s" % (platform.platform(), platform.python_version()),
        "- 结论：%s" % ("全部通过" if all(item[1] for item in results) else "存在失败场景"),
        "",
        "| 场景 | 结果 | 耗时 |",
        "| --- | --- | --- |",
    ]
    for drill, ok, elapsed, _ in results:
        lines.append(
            "| %s | %s | %.2fs |"
            % (drill.name, "PASS" if ok else "FAIL", elapsed)
        )
    lines.append("")

    metrics = [
        (drill.name, metric)
        for drill, _, _, output in results
        if (metric := _metric(output))
    ]
    if metrics:
        lines.extend(["## 关键指标", ""])
        for name, metric in metrics:
            lines.append("- %s：`%s`" % (name, metric))
        lines.append("")

    lines.extend(["## 场景明细", ""])
    for drill, ok, elapsed, output in results:
        lines.extend(
            [
                "### %s" % drill.name,
                "",
                "- 说明：%s" % drill.description,
                "- 命令：`%s`" % " ".join(drill.command),
                "- 结果：%s（%.2fs）" % ("PASS" if ok else "FAIL", elapsed),
                "",
                "```text",
                _tail(output),
                "```",
                "",
            ]
        )

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print("报告已写入 %s" % report_path)
    return 0 if all(item[1] for item in results) else 1


if __name__ == "__main__":
    sys.exit(main())
