"""agent 进程入口：解析参数、单实例锁、日志装配与启动。

进程模型（设计文档 3.1）：
- procd 负责拉起与 respawn
- 主循环每秒 tick，按 interval 触发检测 / 心跳 / 拉配置（见 `app.AgentApp`）
- 内部看门狗在主循环超过 2×检测间隔未 tick 时 os._exit(1)，交由 procd 重启
- 单实例通过 flock 保证
"""

import argparse
import json
import logging
import os
import sys

from .app import AgentApp
from .config import DEFAULT_CONFIG_PATH, ConfigError, load_config
from .lockfile import FileLock
from .runtime import configure_logging

LOGGER = logging.getLogger("vigil.agent")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Vigil OpenWrt 客户端 agent")
    parser.add_argument("--config", default=None, help="配置文件路径")
    parser.add_argument("--once", action="store_true", help="执行一轮检测与心跳后退出")
    parser.add_argument("--no-httpd", action="store_true", help="不启动本地 HTTP 服务")
    parser.add_argument("--log-level", default="INFO", help="日志级别")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    config_path = args.config or os.environ.get("VIGIL_CONFIG") or DEFAULT_CONFIG_PATH
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        sys.stderr.write("配置错误：%s\n" % exc)
        return 2

    lock = FileLock(config["storage"]["lock_path"])
    if not lock.acquire():
        sys.stderr.write("已有 vigil-agent 实例在运行（%s）\n" % config["storage"]["lock_path"])
        return 3

    try:
        app = AgentApp(
            config,
            config_path=config_path,
            httpd_enabled=not args.no_httpd and not args.once,
        )
        storage = config["storage"]
        configure_logging(
            args.log_level,
            storage["log_path"],
            app.state,
            compress=bool(storage.get("log_compress", True)),
            max_bytes=int(storage.get("log_max_bytes") or 2 * 1024 * 1024),
            backups=int(storage.get("log_backups") or 2),
        )
        if not app.storage_persistent:
            LOGGER.warning("持久化存储不可用，已回退到 /tmp，spool 与状态不会跨重启保留")

        if args.once:
            results = app.run_once()
            summary = {layer: result["status"] for layer, result in results.items()}
            sys.stdout.write(json.dumps(summary, ensure_ascii=False) + "\n")
            return 0

        app.start()
        app.run_forever()
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
