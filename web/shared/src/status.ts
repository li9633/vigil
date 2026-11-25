/** 状态语义归一化：后端状态字符串 → 设计令牌色（ok/warn/critical/unknown）+ 中文文案 */

export type UiStatusTone = 'ok' | 'warn' | 'critical' | 'unknown'

const TONES: Record<string, UiStatusTone> = {
  OK: 'ok',
  UP: 'ok',
  HEALTHY: 'ok',
  ONLINE: 'ok',
  PASS: 'ok',
  NORMAL: 'ok',
  RECOVERED: 'ok',
  SUCCESS: 'ok',
  ACKED: 'warn',
  WARN: 'warn',
  WARNING: 'warn',
  SUSPECT: 'warn',
  DEGRADED: 'warn',
  FLAPPING: 'warn',
  BEACON_DOWN: 'warn',
  DEVICE_REBOOT: 'warn',
  CRITICAL: 'critical',
  DOWN: 'critical',
  OFFLINE: 'critical',
  OPEN: 'critical',
  ERROR: 'critical',
  FAIL: 'critical',
  FAILED: 'critical',
  AGENT_DOWN: 'critical',
  HOST_OR_NET_DOWN: 'critical',
  UNKNOWN: 'unknown',
  DISABLED: 'unknown',
  NOT_CONFIGURED: 'unknown',
}

const LABELS: Record<string, string> = {
  OK: '正常',
  UP: '正常',
  HEALTHY: '正常',
  ONLINE: '在线',
  PASS: '通过',
  NORMAL: '正常',
  RECOVERED: '已恢复',
  SUCCESS: '成功',
  ACKED: '已确认',
  WARN: '警告',
  WARNING: '警告',
  SUSPECT: '疑似异常',
  DEGRADED: '降级',
  FLAPPING: '抖动中',
  BEACON_DOWN: 'beacon 缺失',
  DEVICE_REBOOT: '设备重启',
  CRITICAL: '严重',
  DOWN: '离线',
  OFFLINE: '离线',
  OPEN: '进行中',
  ERROR: '错误',
  FAIL: '失败',
  FAILED: '失败',
  AGENT_DOWN: 'agent 离线',
  HOST_OR_NET_DOWN: '主机或网络中断',
  UNKNOWN: '未知',
  DISABLED: '已禁用',
  NOT_CONFIGURED: '未配置',
}

export function normalizeStatus(value: string | null | undefined): UiStatusTone {
  if (!value) return 'unknown'
  return TONES[String(value).toUpperCase()] ?? 'unknown'
}

export function statusLabel(value: string | null | undefined): string {
  if (!value) return '未知'
  const key = String(value).toUpperCase()
  return LABELS[key] ?? String(value)
}

/** 布尔状态（如服务端连通性）→ ok/critical */
export function booleanStatus(value: boolean | null | undefined): UiStatusTone {
  if (value === null || value === undefined) return 'unknown'
  return value ? 'ok' : 'critical'
}
