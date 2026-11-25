#!/bin/sh
# Vigil 外部脚本模块示例。
#
# 放置位置：/etc/vigil/modules.d/（权限 0700）
# 契约：
#   - stdout 必须是一行 JSON
#   - stderr 会被截断保留（最多 4 KB）
#   - 退出码：0=OK，1=WARN，2=CRITICAL，3=UNKNOWN
#   - 参数通过环境变量传入，禁止读取交互式 stdin
#   - 由 agent 以 root 执行，脚本内容需经本地白名单与 SHA-256 校验

uptime_seconds=$(cut -d. -f1 /proc/uptime 2>/dev/null)
case "$uptime_seconds" in
    ''|*[!0-9]*) uptime_seconds=0 ;;
esac

printf '{"status":"ok","uptime":%s}\n' "$uptime_seconds"
exit 0
