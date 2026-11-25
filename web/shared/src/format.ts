/** 时间与数值格式化（服务端时间为权威时间，统一按本地时区展示） */

export type TimeInput = string | number | Date | null | undefined

export function toDate(value: TimeInput): Date | null {
  if (value === null || value === undefined || value === '') return null
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value
  if (typeof value === 'number') {
    const ms = value < 1e12 ? value * 1000 : value
    const date = new Date(ms)
    return Number.isNaN(date.getTime()) ? null : date
  }
  const text = value.trim()
  if (!text) return null
  if (/^\d+(\.\d+)?$/.test(text)) return toDate(Number(text))
  const date = new Date(text)
  return Number.isNaN(date.getTime()) ? null : date
}

function pad2(value: number): string {
  return value < 10 ? `0${value}` : String(value)
}

export function formatDateTime(value: TimeInput, fallback = '—'): string {
  const date = toDate(value)
  if (!date) return fallback
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(
    date.getHours(),
  )}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`
}

export function formatTime(value: TimeInput, fallback = '—'): string {
  const date = toDate(value)
  if (!date) return fallback
  return `${pad2(date.getHours())}:${pad2(date.getMinutes())}`
}

export function formatRelative(value: TimeInput, now: number = Date.now(), fallback = '—'): string {
  const date = toDate(value)
  if (!date) return fallback
  const diffSeconds = Math.round((now - date.getTime()) / 1000)
  if (diffSeconds < -30) {
    const ahead = Math.abs(diffSeconds)
    return ahead < 60 ? `${ahead} 秒后` : `${Math.round(ahead / 60)} 分钟后`
  }
  if (diffSeconds < 10) return '刚刚'
  if (diffSeconds < 60) return `${diffSeconds} 秒前`
  if (diffSeconds < 3600) return `${Math.floor(diffSeconds / 60)} 分钟前`
  if (diffSeconds < 86400) return `${Math.floor(diffSeconds / 3600)} 小时前`
  if (diffSeconds < 86400 * 30) return `${Math.floor(diffSeconds / 86400)} 天前`
  return formatDateTime(date)
}

/** 秒 → 人类可读时长，用于 uptime / 持续时长 */
export function formatDuration(seconds: number | null | undefined, fallback = '—'): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds) || seconds < 0) {
    return fallback
  }
  const total = Math.floor(seconds)
  if (total < 60) return `${total} 秒`
  const minutes = Math.floor(total / 60)
  if (minutes < 60) return `${minutes} 分 ${total % 60} 秒`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时 ${minutes % 60} 分`
  const days = Math.floor(hours / 24)
  return `${days} 天 ${hours % 24} 小时`
}

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

/** mm:ss 冷却倒计时 */
export function formatCountdown(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  return `${pad2(Math.floor(total / 60))}:${pad2(total % 60)}`
}
