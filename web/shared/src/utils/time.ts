/**
 * 通用时间工具。
 *
 * 约定：
 * - 持久化（日志、数据库、事件、API）只能使用绝对时间，禁止相对时间。
 * - 相对时间只用于 UI 展示；超过 maxAgeYears 自动回退为完整日期时间。
 * - 月份按 30 天、年份按 365 天折算，保证跨端结果一致。
 */

export type TimeInput = string | number | Date | null | undefined

const SECOND = 1
const MINUTE = 60
const HOUR = 3600
const DAY = 86400
const MONTH = 30 * DAY
const YEAR = 365 * DAY
const DEFAULT_MAX_AGE_YEARS = 3

export function toDate(value: TimeInput): Date | null {
  if (value === null || value === undefined || value === '') return null
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value
  if (typeof value === 'number') {
    const ms = Math.abs(value) < 1e12 ? value * 1000 : value
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

export function now(): Date {
  return new Date()
}

export function today(): Date {
  const date = new Date()
  date.setHours(0, 0, 0, 0)
  return date
}

export function currentTime(): string {
  return formatTime(now())
}

export function nowIso(): string {
  return new Date().toISOString()
}

export function toIso(value: TimeInput): string | undefined {
  return toDate(value)?.toISOString()
}

export function fromIso(text: string | null | undefined): Date | null {
  if (!text) return null
  return toDate(text)
}

export function parseDateTime(text: string | null | undefined): Date | null {
  return fromIso(text)
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

function pastSeconds(seconds: number): string {
  if (seconds < 10) return '刚刚'
  if (seconds < MINUTE) return `${Math.floor(seconds)} 秒前`
  if (seconds < HOUR) return `${Math.floor(seconds / MINUTE)} 分钟前`
  if (seconds < DAY) return `${Math.floor(seconds / HOUR)} 小时前`
  if (seconds < MONTH) return `${Math.floor(seconds / DAY)} 天前`
  if (seconds < YEAR) return `${Math.floor(seconds / MONTH)} 个月前`
  return `${Math.floor(seconds / YEAR)} 年前`
}

function futureSeconds(seconds: number): string {
  if (seconds < 10) return '即将'
  if (seconds < MINUTE) return `${Math.floor(seconds)} 秒后`
  if (seconds < HOUR) return `${Math.floor(seconds / MINUTE)} 分钟后`
  if (seconds < DAY) return `${Math.floor(seconds / HOUR)} 小时后`
  if (seconds < MONTH) return `${Math.floor(seconds / DAY)} 天后`
  if (seconds < YEAR) return `${Math.floor(seconds / MONTH)} 个月后`
  return `${Math.floor(seconds / YEAR)} 年后`
}

function maxAgeSeconds(maxAgeYears: number): number {
  return Math.max(0, maxAgeYears) * YEAR
}

/** 相对过去时间；未来时间自动交给 formatRelativeFuture。 */
export function formatRelative(
  value: TimeInput,
  nowMs: number = Date.now(),
  fallback = '—',
  maxAgeYears = DEFAULT_MAX_AGE_YEARS,
): string {
  const date = toDate(value)
  if (!date) return fallback
  const diffSeconds = (nowMs - date.getTime()) / 1000
  if (diffSeconds < 0) {
    return formatRelativeFuture(value, nowMs, fallback, maxAgeYears)
  }
  if (maxAgeYears <= 0 || diffSeconds > maxAgeSeconds(maxAgeYears)) {
    return formatDateTime(date, fallback)
  }
  return pastSeconds(diffSeconds)
}

/** 相对未来时间；过去时间自动交给 formatRelative。 */
export function formatRelativeFuture(
  value: TimeInput,
  nowMs: number = Date.now(),
  fallback = '—',
  maxAgeYears = DEFAULT_MAX_AGE_YEARS,
): string {
  const date = toDate(value)
  if (!date) return fallback
  const diffSeconds = (date.getTime() - nowMs) / 1000
  if (diffSeconds < 0) {
    return formatRelative(value, nowMs, fallback, maxAgeYears)
  }
  if (maxAgeYears <= 0 || diffSeconds > maxAgeSeconds(maxAgeYears)) {
    return formatDateTime(date, fallback)
  }
  return futureSeconds(diffSeconds)
}

/** 秒 → 人类可读时长，用于 uptime / 持续时长。 */
export function formatDuration(seconds: number | null | undefined, fallback = '—'): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds) || seconds < 0) {
    return fallback
  }
  const total = Math.floor(seconds)
  if (total < MINUTE) return `${total} 秒`
  const minutes = Math.floor(total / MINUTE)
  if (minutes < 60) return `${minutes} 分 ${total % 60} 秒`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时 ${minutes % 60} 分`
  const days = Math.floor(hours / 24)
  return `${days} 天 ${hours % 24} 小时`
}

/** mm:ss 冷却倒计时。 */
export function formatCountdown(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  return `${pad2(Math.floor(total / 60))}:${pad2(total % 60)}`
}
