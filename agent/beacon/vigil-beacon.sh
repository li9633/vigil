#!/bin/sh
# Vigil 最小心跳（beacon）
#
# 由 cron 每分钟拉起一次，不依赖 Python：即使 vigil-agent 崩溃，
# beacon 仍能证明「主机与网络正常」，供服务端区分 AGENT_DOWN 与 HOST_OR_NET_DOWN。
#
# 依赖：uclient-fetch（OpenWrt 默认提供）
# 配置：/etc/vigil/config.json 中的 server.base_url / client_id / secret
# 退出码：0 成功或未配置；1 发送失败（交给 cron 记录）

set -u

CONF="${VIGIL_CONFIG:-/etc/vigil/config.json}"
STATE_DIR="${VIGIL_RUN_DIR:-/var/run/vigil}"

[ -r "$CONF" ] || exit 0

# 从 JSON 中按行提取字符串字段（配置中键名唯一，够用且不引入 jq 依赖）
json_str() {
    sed -n 's/.*"'"$1"'"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$CONF" | head -n 1
}

BASE_URL=$(json_str base_url)
CLIENT_ID=$(json_str client_id)
SECRET=$(json_str secret)

[ -n "$BASE_URL" ] || exit 0
[ -n "$CLIENT_ID" ] || exit 0
[ -n "$SECRET" ] || exit 0

BASE_URL="${BASE_URL%/}"
mkdir -p "$STATE_DIR" 2>/dev/null

# 单调递增 seq：重启后继续累加，便于服务端发现丢包
SEQ=0
if [ -r "$STATE_DIR/beacon.seq" ]; then
    SEQ=$(cat "$STATE_DIR/beacon.seq" 2>/dev/null)
fi
case "$SEQ" in ''|*[!0-9]*) SEQ=0 ;; esac
SEQ=$((SEQ + 1))
printf '%s\n' "$SEQ" > "$STATE_DIR/beacon.seq" 2>/dev/null

# boot_id：优先内核，其次持久化随机值
BOOT_ID=""
if [ -r /proc/sys/kernel/random/boot_id ]; then
    BOOT_ID=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)
fi
if [ -z "$BOOT_ID" ]; then
    if [ -r "$STATE_DIR/boot_id" ]; then
        BOOT_ID=$(cat "$STATE_DIR/boot_id" 2>/dev/null)
    else
        BOOT_ID=$(cat /proc/sys/kernel/random/uuid 2>/dev/null || echo "beacon-$$-$(date +%s)")
        printf '%s\n' "$BOOT_ID" > "$STATE_DIR/boot_id" 2>/dev/null
    fi
fi

UPTIME=$(cut -d. -f1 /proc/uptime 2>/dev/null)
case "$UPTIME" in ''|*[!0-9]*) UPTIME=0 ;; esac

TS=$(date +%s)
NONCE=$(hexdump -v -n 16 -e '/1 "%02x"' /dev/urandom 2>/dev/null)
[ -n "$NONCE" ] || NONCE="$$-${TS}-$(cat /proc/sys/kernel/random/uuid 2>/dev/null)"

BODY=$(printf '{"client_id":"%s","seq":%s,"boot_id":"%s","uptime":%s}' \
    "$CLIENT_ID" "$SEQ" "$BOOT_ID" "$UPTIME")

uclient-fetch -q -T 5 -O /dev/null \
    --header "Content-Type: application/json" \
    --header "X-Vigil-Client: $CLIENT_ID" \
    --header "Authorization: Bearer $SECRET" \
    --header "X-Vigil-Timestamp: $TS" \
    --header "X-Vigil-Nonce: $NONCE" \
    --post-data "$BODY" \
    "$BASE_URL/api/v1/beacon" >/dev/null 2>&1 || {
    logger -t vigil-beacon "beacon 发送失败 seq=$SEQ" 2>/dev/null
    exit 1
}

exit 0
