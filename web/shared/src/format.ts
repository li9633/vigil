/** 时间与数值格式化（服务端时间为权威时间，统一按本地时区展示） */

export {
  toDate,
  now,
  today,
  currentTime,
  nowIso,
  toIso,
  fromIso,
  parseDateTime,
  formatDateTime,
  formatTime,
  formatRelative,
  formatRelativeFuture,
  formatDuration,
  formatCountdown,
  type TimeInput,
} from './utils/time'

/** 0.98 → 98.0%（入参同时兼容 0-1 与 0-100） */
export function formatPercent(value: number | null | undefined, digits = 1, fallback = '—'): string {
  if (value === null || value === undefined || Number.isNaN(value)) return fallback
  const normalized = value <= 1 ? value * 100 : value
  return `${normalized.toFixed(digits)}%`
}

export function formatLatency(value: number | null | undefined, fallback = '—'): string {
  if (value === null || value === undefined || Number.isNaN(value)) return fallback
  return `${Math.round(value)} ms`
}
