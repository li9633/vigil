import { defineStore } from 'pinia'

const STORAGE_KEY = 'vigil.client.session'

/**
 * 客户端会话由 agent 通过 HttpOnly Cookie 维持；这里只记录已登录标记，
 * 401 时由统一请求层跳回登录页。
 */
export const useAuthStore = defineStore('client-auth', {
  state: () => ({
    signedIn: sessionStorage.getItem(STORAGE_KEY) === '1',
  }),
  getters: {
    isAuthenticated: (state): boolean => state.signedIn,
  },
  actions: {
    signIn() {
      this.signedIn = true
      sessionStorage.setItem(STORAGE_KEY, '1')
    },
    signOut() {
      this.signedIn = false
      sessionStorage.removeItem(STORAGE_KEY)
    },
  },
})
