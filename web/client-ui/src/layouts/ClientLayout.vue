<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const tabs = [
  { to: '/', label: '状态' },
  { to: '/checks', label: '检测' },
  { to: '/events', label: '事件' },
  { to: '/actions', label: '动作' },
  { to: '/modules', label: '模块' },
  { to: '/logs', label: '日志' },
  { to: '/settings', label: '设置' },
]

const pageTitle = computed(() => (route.meta.title as string | undefined) ?? 'Vigil')

function isActive(to: string): boolean {
  return to === '/' ? route.path === '/' : route.path === to || route.path.startsWith(`${to}/`)
}

function logout() {
  auth.signOut()
  router.replace({ name: 'login' })
}
</script>

<template>
  <div class="client-shell">
    <header class="client-header">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true">V</span>
        <span class="brand-text">
          <strong>Vigil</strong>
          <small>{{ pageTitle }}</small>
        </span>
      </div>
      <button class="logout-btn" type="button" @click="logout">退出</button>
    </header>

    <nav class="tabbar" aria-label="客户端导航">
      <router-link
        v-for="tab in tabs"
        :key="tab.to"
        :to="tab.to"
        class="tab"
        :class="{ active: isActive(tab.to) }"
      >
        {{ tab.label }}
      </router-link>
    </nav>

    <main class="client-content">
      <router-view />
    </main>
  </div>
</template>

<style scoped lang="scss">
.client-shell {
  min-height: 100vh;
  background: var(--vigil-color-bg);
}

.client-header {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--vigil-space-3);
  height: 52px;
  padding: 0 var(--vigil-space-3);
  background: var(--vigil-color-sidebar-bg);
  color: #fff;
}

.brand {
  display: flex;
  align-items: center;
  gap: var(--vigil-space-2);
  min-width: 0;
}

.brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: var(--vigil-radius-sm);
  background: linear-gradient(135deg, #2563eb, #38bdf8);
  font-size: var(--vigil-font-sm);
  font-weight: 700;
  flex: 0 0 auto;
}

.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
  min-width: 0;

  strong {
    font-size: var(--vigil-font-sm);
    letter-spacing: 0.06em;
  }

  small {
    font-size: 10px;
    color: rgba(199, 210, 223, 0.7);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.logout-btn {
  flex: 0 0 auto;
  border: 1px solid rgba(255, 255, 255, 0.2);
  background: transparent;
  color: rgba(255, 255, 255, 0.82);
  border-radius: var(--vigil-radius-sm);
  padding: 4px 10px;
  font-size: var(--vigil-font-xs);
  cursor: pointer;

  &:hover {
    background: rgba(255, 255, 255, 0.08);
  }
}

.tabbar {
  position: sticky;
  top: 52px;
  z-index: 15;
  display: flex;
  gap: 2px;
  overflow-x: auto;
  padding: 0 var(--vigil-space-2);
  background: var(--vigil-color-surface);
  border-bottom: 1px solid var(--vigil-color-border);
  scrollbar-width: none;

  &::-webkit-scrollbar {
    display: none;
  }
}

.tab {
  flex: 0 0 auto;
  padding: 10px 10px 8px;
  font-size: var(--vigil-font-xs);
  color: var(--vigil-color-text-secondary);
  border-bottom: 2px solid transparent;
  white-space: nowrap;

  &.active {
    color: var(--vigil-color-primary);
    border-bottom-color: var(--vigil-color-primary);
    font-weight: 600;
  }
}

.client-content {
  width: 100%;
  max-width: 760px;
  margin: 0 auto;
  padding: var(--vigil-space-3);
  padding-bottom: calc(var(--vigil-space-6) + env(safe-area-inset-bottom));
}

@media (max-width: 374px) {
  .client-content {
    padding: var(--vigil-space-2);
  }
}
</style>
