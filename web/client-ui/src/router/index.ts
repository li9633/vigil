import { createRouter, createWebHistory } from 'vue-router'
import ClientLayout from '@/layouts/ClientLayout.vue'
import { useAuthStore } from '@/stores/auth'

declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    public?: boolean
  }
}

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { title: '登录', public: true },
    },
    {
      path: '/',
      component: ClientLayout,
      children: [
        {
          path: '',
          name: 'home',
          component: () => import('@/views/HomeView.vue'),
          meta: { title: '状态' },
        },
        {
          path: 'guards',
          name: 'guards',
          component: () => import('@/views/GuardsView.vue'),
          meta: { title: '守护项' },
        },
        {
          path: 'checks',
          name: 'checks',
          component: () => import('@/views/ChecksView.vue'),
          meta: { title: '检测项' },
        },
        {
          path: 'events',
          name: 'events',
          component: () => import('@/views/EventsView.vue'),
          meta: { title: '事件' },
        },
        {
          path: 'actions',
          name: 'actions',
          component: () => import('@/views/ActionsView.vue'),
          meta: { title: '手动动作' },
        },
        {
          path: 'modules',
          name: 'modules',
          component: () => import('@/views/ModulesView.vue'),
          meta: { title: '模块管理' },
        },
        {
          path: 'logs',
          name: 'logs',
          component: () => import('@/views/LogsView.vue'),
          meta: { title: '日志' },
        },
        {
          path: 'settings',
          name: 'settings',
          component: () => import('@/views/SettingsView.vue'),
          meta: { title: '设置' },
        },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach((to) => {
  const auth = useAuthStore()

  if (to.meta.public) {
    // 401 跳转登录页时会带上 redirect 参数，此时即使本地仍有登录标记也应停留在登录页
    if (to.name === 'login' && auth.isAuthenticated && !to.query.redirect) {
      return { name: 'home' }
    }
    return true
  }

  if (!auth.isAuthenticated) {
    return {
      name: 'login',
      query: to.fullPath === '/' ? {} : { redirect: to.fullPath },
    }
  }

  return true
})

export default router
