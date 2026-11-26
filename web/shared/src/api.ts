/**
 * 统一 fetch 封装：
 * - 集中管理 baseURL / credentials / 超时
 * - 统一解析错误体并抛出 ApiError
 * - 401 时跳转登录页（带 redirect 参数，且避免重复跳转）
 */
import type { ListResponse } from './types'

export type QueryValue = string | number | boolean | null | undefined

export interface ApiConfig {
  /** 接口前缀，例如 '' 或 'http://127.0.0.1:8000' */
  baseURL: string
  credentials: RequestCredentials
  /** 401 时跳转的登录路径，例如 '/login'；为空表示不跳转 */
  unauthorizedRedirect?: string
  timeoutMs: number
}

export interface RequestOptions {
  query?: Record<string, QueryValue>
  headers?: Record<string, string>
  signal?: AbortSignal
  /** 登录接口等场景：401 不跳转，交给页面展示错误 */
  skipAuthRedirect?: boolean
  timeoutMs?: number
  credentials?: RequestCredentials
}

export class ApiError extends Error {
  readonly status: number
  readonly code?: string
  readonly payload?: unknown

  constructor(message: string, status = 0, code?: string, payload?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.payload = payload
  }
}

let config: ApiConfig = {
  baseURL: '',
  credentials: 'same-origin',
  timeoutMs: 15000,
}

let redirecting = false

export function configureApi(next: Partial<ApiConfig>): void {
  config = {
    ...config,
    ...next,
    baseURL: (next.baseURL ?? config.baseURL).replace(/\/+$/, ''),
  }
}

export function getApiConfig(): Readonly<ApiConfig> {
  return config
}

export function getErrorMessage(error: unknown, fallback = '请求失败，请稍后重试'): string {
  if (error instanceof ApiError) return error.message || fallback
  if (error instanceof Error && error.message) return error.message
  if (typeof error === 'string' && error.trim()) return error
  return fallback
}

/** 兼容裸数组与 { items: [] } 两种列表返回 */
export function unwrapList<T>(payload: ListResponse<T> | null | undefined): T[] {
  if (!payload) return []
  if (Array.isArray(payload)) return payload
  return Array.isArray(payload.items) ? payload.items : []
}

/**
 * 解包统一响应 `{code, message, data}`。
 * 迁移期兼容尚未包装的裸响应：没有 code/data 字段时原样返回。
 */
function unwrapEnvelope(payload: unknown): unknown {
  if (!payload || typeof payload !== 'object') return payload
  const body = payload as Record<string, unknown>
  if (typeof body.code !== 'string' || !('data' in body)) return payload
  if (body.code === 'ok') return body.data
  throw new ApiError(
    typeof body.message === 'string' && body.message ? body.message : '请求失败',
    0,
    body.code,
    body,
  )
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const isAbsolute = /^https?:\/\//i.test(path)
  let url = isAbsolute ? path : `${config.baseURL}${path.startsWith('/') ? '' : '/'}${path}`
  if (!query) return url
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined || value === '') continue
    params.append(key, String(value))
  }
  const qs = params.toString()
  if (!qs) return url
  url += (url.includes('?') ? '&' : '?') + qs
  return url
}

function handleUnauthorized(): void {
  const target = config.unauthorizedRedirect
  if (!target || redirecting) return
  if (typeof window === 'undefined') return
  if (window.location.pathname.startsWith(target)) return
  redirecting = true
  const current = `${window.location.pathname}${window.location.search}`
  window.location.assign(`${target}?redirect=${encodeURIComponent(current)}`)
  window.setTimeout(() => {
    redirecting = false
  }, 1500)
}

function extractMessage(payload: unknown, fallback: string): string {
  if (typeof payload === 'string' && payload.trim()) return payload.trim()
  if (payload && typeof payload === 'object') {
    const body = payload as Record<string, unknown>
    if (typeof body.detail === 'string' && body.detail.trim()) return body.detail.trim()
    if (Array.isArray(body.detail)) {
      const parts = body.detail
        .map((item) => {
          if (item && typeof item === 'object') {
            const detail = item as Record<string, unknown>
            if (typeof detail.msg === 'string' && detail.msg.trim()) return detail.msg.trim()
          }
          return ''
        })
        .filter(Boolean)
      if (parts.length) return parts.join('；')
    }
    for (const key of ['message', 'error', 'reason'] as const) {
      const value = body[key]
      if (typeof value === 'string' && value.trim()) return value.trim()
    }
  }
  return fallback
}

function extractCode(payload: unknown): string | undefined {
  if (payload && typeof payload === 'object') {
    const body = payload as Record<string, unknown>
    for (const key of ['code', 'error_code'] as const) {
      const value = body[key]
      if (typeof value === 'string' && value.trim()) return value
    }
  }
  return undefined
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  options: RequestOptions = {},
): Promise<T> {
  const timeoutMs = options.timeoutMs ?? config.timeoutMs
  const controller = new AbortController()
  let timedOut = false
  let abortedByUser = false

  const timer = window.setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)

  const onUserAbort = () => {
    abortedByUser = true
    controller.abort()
  }
  options.signal?.addEventListener('abort', onUserAbort, { once: true })
  if (options.signal?.aborted) onUserAbort()

  const headers: Record<string, string> = { Accept: 'application/json', ...options.headers }
  let payload: BodyInit | undefined

  if (body !== undefined && body !== null) {
    if (typeof body === 'string' || body instanceof FormData || body instanceof Blob) {
      payload = body as BodyInit
    } else {
      headers['Content-Type'] = 'application/json'
      payload = JSON.stringify(body)
    }
  }

  try {
    const response = await fetch(buildUrl(path, options.query), {
      method,
      headers,
      body: payload,
      signal: controller.signal,
      credentials: options.credentials ?? config.credentials,
    })

    if (response.status === 401 && !options.skipAuthRedirect) handleUnauthorized()

    let data: unknown = null
    if (response.status !== 204 && response.status !== 205) {
      const text = await response.text()
      if (text) {
        const contentType = response.headers.get('content-type') ?? ''
        if (contentType.includes('json')) {
          try {
            data = JSON.parse(text)
          } catch {
            data = text
          }
        } else {
          data = text
        }
      }
    }

    if (!response.ok) {
      const fallback = `请求失败（HTTP ${response.status}）`
      throw new ApiError(extractMessage(data, fallback), response.status, extractCode(data), data)
    }

    return unwrapEnvelope(data) as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (timedOut) throw new ApiError('请求超时，请检查网络', 0, 'timeout')
    if (abortedByUser) throw new ApiError('请求已取消', 0, 'aborted')
    throw new ApiError('网络异常，无法连接服务', 0, 'network', error)
  } finally {
    window.clearTimeout(timer)
    options.signal?.removeEventListener('abort', onUserAbort)
  }
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) => request<T>('GET', path, undefined, options),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>('POST', path, body, options),
  put: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>('PUT', path, body, options),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>('PATCH', path, body, options),
  delete: <T>(path: string, options?: RequestOptions) => request<T>('DELETE', path, undefined, options),
}
