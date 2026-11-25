import { computed, readonly, ref } from 'vue'

/** 主题三态：跟随系统 / 强制浅色 / 强制深色 */
export type ThemeMode = 'auto' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'

const STORAGE_KEY = 'vigil-theme'
const THEME_COLOR: Record<ResolvedTheme, string> = {
  light: '#F6F3EC',
  dark: '#0B0912',
}

const mode = ref<ThemeMode>('auto')
const resolved = ref<ResolvedTheme>('light')

let initialized = false
let media: MediaQueryList | null = null

function readStored(): ThemeMode {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    if (value === 'light' || value === 'dark' || value === 'auto') return value
  } catch {
    // 隐私模式下 localStorage 可能不可用
  }
  return 'auto'
}

function prefersDark(): boolean {
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

function resolve(current: ThemeMode): ResolvedTheme {
  if (current === 'auto') return prefersDark() ? 'dark' : 'light'
  return current
}

function apply(next: ResolvedTheme) {
  const root = document.documentElement
  root.classList.toggle('dark', next === 'dark')
  root.dataset.theme = next
  root.style.colorScheme = next

  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', THEME_COLOR[next])

  resolved.value = next
}

function sync() {
  apply(resolve(mode.value))
}

/** 设置主题并持久化；auto 跟随系统 */
export function setTheme(next: ThemeMode) {
  mode.value = next
  try {
    localStorage.setItem(STORAGE_KEY, next)
  } catch {
    // 忽略存储失败，仅当前会话生效
  }
  sync()
}

/** 顶栏快捷二态切换：按当前解析结果取反，并写入显式值 */
export function toggleTheme() {
  setTheme(resolved.value === 'dark' ? 'light' : 'dark')
}

/** 应用启动时调用一次：恢复选择、监听系统与跨标签变化 */
export function initTheme() {
  if (initialized || typeof window === 'undefined') return
  initialized = true

  mode.value = readStored()
  sync()

  media = window.matchMedia('(prefers-color-scheme: dark)')
  media.addEventListener('change', () => {
    if (mode.value === 'auto') sync()
  })

  window.addEventListener('storage', (event) => {
    if (event.key !== STORAGE_KEY) return
    mode.value = readStored()
    sync()
  })
}

export function useTheme() {
  return {
    mode: readonly(mode),
    resolved: readonly(resolved),
    isDark: computed(() => resolved.value === 'dark'),
    setTheme,
    toggleTheme,
  }
}
