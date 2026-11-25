import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'

export interface UsePollingOptions {
  /** 轮询间隔（毫秒），0 表示不自动轮询 */
  interval?: number
  /** 挂载后立即执行一次，默认 true */
  immediate?: boolean
  /** 任务不抛错时，用它判断本次是否失败（页面自行管理 error 字符串） */
  isError?: () => boolean
}

const FIRST_RETRY_DELAY = 15_000
const SECOND_RETRY_DELAY = 30_000
const THIRD_RETRY_DELAY = 60_000
const MAX_FAILURES = 3

/**
 * 统一轮询：可见性感知、并发保护、失败退避、暂停与恢复。
 * task 可以自行管理数据与错误；composable 负责节奏与新鲜度。
 */
export function usePolling(
  task: () => Promise<unknown> | unknown,
  options: UsePollingOptions = {},
) {
  const interval = options.interval ?? 0
  const loading = ref(false)
  const refreshing = ref(false)
  const lastUpdated = ref<Date | null>(null)
  const failures = ref(0)
  const paused = ref(false)

  let timer: number | null = null

  function clearTimer() {
    if (timer !== null) {
      window.clearTimeout(timer)
      timer = null
    }
  }

  function retryDelay(): number {
    if (failures.value <= 0) return interval
    if (failures.value === 1) return Math.max(interval, FIRST_RETRY_DELAY)
    if (failures.value === 2) return Math.max(interval, SECOND_RETRY_DELAY)
    return Math.max(interval, THIRD_RETRY_DELAY)
  }

  function schedule() {
    clearTimer()
    if (interval <= 0 || paused.value) return
    if (document.visibilityState === 'hidden') return
    timer = window.setTimeout(() => {
      void run()
    }, retryDelay())
  }

  function markFailure() {
    failures.value += 1
    if (failures.value >= MAX_FAILURES) paused.value = true
  }

  async function run(): Promise<void> {
    if (inFlight) return
    inFlight = true
    clearTimer()
    refreshing.value = true
    if (!hasLoaded) loading.value = true
    try {
      await task()
      if (disposed) return
      hasLoaded = true
      lastUpdated.value = new Date()
      if (options.isError?.()) {
        markFailure()
      } else {
        failures.value = 0
        paused.value = false
      }
    } catch {
      if (!disposed) markFailure()
    } finally {
      inFlight = false
      refreshing.value = false
      loading.value = false
      if (!disposed) schedule()
    }
  }

  /** 手动刷新：重置退避与暂停状态，立即执行一次并重置计时 */
  async function refresh() {
    paused.value = false
    failures.value = 0
    await run()
  }

  /** 失败后重试：与 refresh 同一入口 */
  async function retry() {
    await refresh()
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible') {
      if (!paused.value) void run()
    } else {
      clearTimer()
    }
  }

  let disposed = false
  let inFlight = false
  let hasLoaded = false

  onMounted(() => {
    document.addEventListener('visibilitychange', onVisibilityChange)
    if (options.immediate !== false) void run()
  })

  onBeforeUnmount(() => {
    disposed = true
    clearTimer()
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  // 用 reactive 包裹，模板里 poll.xxx 直接拿到解包后的值
  return reactive({
    loading,
    refreshing,
    lastUpdated,
    paused,
    failures,
    refresh,
    retry,
    run,
  })
}
