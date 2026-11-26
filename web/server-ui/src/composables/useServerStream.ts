/**
 * 管理端 SSE 订阅：服务端在数据版本变化时推送 update 事件。
 *
 * 与 usePolling 配合使用：SSE 只负责「通知有变化」，收到事件后仍由 REST 拉取数据，
 * 避免把序列化逻辑复制到推送通道。浏览器会自动按服务端的 retry 建议重连。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { getApiConfig } from '@vigil/shared'

export interface UseServerStreamOptions {
  /** 收到更新事件后的回调，通常是触发一次 REST 刷新 */
  onUpdate?: () => void | Promise<void>
  /** 两次触发之间的最小间隔（毫秒），避免同一批变更重复拉取 */
  throttleMs?: number
  /** 是否启用，默认 true */
  enabled?: boolean
}

const DEFAULT_THROTTLE_MS = 1500
const RECONNECT_DELAY = 5000

export function useServerStream(options: UseServerStreamOptions = {}) {
  const connected = ref(false)
  const supported = typeof window !== 'undefined' && 'EventSource' in window
  const throttleMs = options.throttleMs ?? DEFAULT_THROTTLE_MS

  let source: EventSource | null = null
  let disposed = false
  let lastFiredAt = 0
  let pendingTimer: number | null = null
  let reconnectTimer: number | null = null

  function streamUrl(): string {
    return `${getApiConfig().baseURL}/api/v1/admin/stream`
  }

  function fire() {
    if (!options.onUpdate) return
    // 后台标签页不做无谓拉取；usePolling 会在回到前台时补一次刷新
    if (document.visibilityState === 'hidden') return
    const wait = throttleMs - (Date.now() - lastFiredAt)
    if (wait > 0) {
      if (pendingTimer !== null) return
      pendingTimer = window.setTimeout(() => {
        pendingTimer = null
        fire()
      }, wait)
      return
    }
    lastFiredAt = Date.now()
    void options.onUpdate()
  }

  function clearTimers() {
    if (pendingTimer !== null) {
      window.clearTimeout(pendingTimer)
      pendingTimer = null
    }
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
  }

  function close() {
    source?.close()
    source = null
  }

  function connect() {
    if (disposed || !supported) return
    clearTimers()
    close()
    const es = new EventSource(streamUrl(), { withCredentials: true })
    source = es
    es.addEventListener('ready', () => {
      connected.value = true
    })
    es.addEventListener('update', () => {
      connected.value = true
      fire()
    })
    es.onerror = () => {
      connected.value = false
      // 浏览器会自动重连；连接被彻底关闭时手动重建，避免落到静默失败
      if (es.readyState === EventSource.CLOSED && reconnectTimer === null) {
        reconnectTimer = window.setTimeout(() => {
          reconnectTimer = null
          if (!disposed && !connected.value) connect()
        }, RECONNECT_DELAY)
      }
    }
  }

  function onVisibilityChange() {
    if (document.visibilityState !== 'visible') return
    // 后台标签页可能被浏览器断开连接，回到前台时补一次健康检查
    if (!source || source.readyState === EventSource.CLOSED) connect()
  }

  onMounted(() => {
    if (options.enabled === false) return
    document.addEventListener('visibilitychange', onVisibilityChange)
    connect()
  })

  onBeforeUnmount(() => {
    disposed = true
    document.removeEventListener('visibilitychange', onVisibilityChange)
    clearTimers()
    close()
  })

  return { connected, supported, reconnect: connect }
}
