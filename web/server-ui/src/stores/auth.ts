import { defineStore } from 'pinia'

const STORAGE_KEY = 'vigil.admin.session'

interface AdminSession {
  username: string
  signedAt: number
}

function readSession(): AdminSession | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<AdminSession>
    if (!parsed || typeof parsed.username !== 'string') return null
    return { username: parsed.username, signedAt: Number(parsed.signedAt) || Date.now() }
  } catch {
    return null
  }
}

/**
 * 会话由后端 HttpOnly Cookie 维持，store 仅保存展示用的用户名，
 * 真正的失效由 401 统一跳转登录页兜底。
 */
export const useAuthStore = defineStore('admin-auth', {
  state: () => {
    const session = readSession()
    return {
      username: session?.username ?? '',
      signedIn: Boolean(session),
    }
  },
  getters: {
    isAuthenticated: (state): boolean => state.signedIn,
  },
  actions: {
    signIn(username: string) {
      this.username = username
      this.signedIn = true
      sessionStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ username, signedAt: Date.now() } satisfies AdminSession),
      )
    },
    signOut() {
      this.username = ''
      this.signedIn = false
      sessionStorage.removeItem(STORAGE_KEY)
    },
  },
})
