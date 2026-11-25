<script setup lang="ts">
import { useRoute } from 'vue-router'

interface NavItem {
  to: string
  label: string
}

const items: NavItem[] = [
  { to: '/', label: '状态' },
  { to: '/guards', label: '守护项' },
  { to: '/checks', label: '检测项' },
  { to: '/events', label: '事件' },
  { to: '/actions', label: '手动动作' },
  { to: '/modules', label: '模块管理' },
  { to: '/logs', label: '日志' },
  { to: '/settings', label: '设置' },
]

const route = useRoute()

function isActive(to: string): boolean {
  return to === '/' ? route.path === '/' : route.path === to || route.path.startsWith(`${to}/`)
}
</script>

<template>
  <nav class="nav-list" aria-label="客户端导航">
    <router-link
      v-for="item in items"
      :key="item.to"
      :to="item.to"
      class="nav-item"
      :class="{ active: isActive(item.to) }"
    >
      <span class="nav-dot" aria-hidden="true"></span>
      <span class="nav-label">{{ item.label }}</span>
    </router-link>
  </nav>
</template>

<style scoped lang="scss">
.nav-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--vigil-space-2);
}

.nav-item {
  display: flex;
  align-items: center;
  gap: var(--vigil-space-2);
  padding: 10px 12px;
  border-radius: var(--vigil-radius-sm);
  color: var(--vigil-color-sidebar-text);
  font-size: var(--vigil-font-sm);
  transition: background var(--vigil-transition), color var(--vigil-transition);

  &:hover {
    background: rgba(255, 255, 255, 0.06);
    color: #fff;
  }

  &.active {
    background: rgba(37, 99, 235, 0.22);
    color: #fff;
    font-weight: 600;
  }
}

.nav-dot {
  flex: 0 0 auto;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  opacity: 0.45;
}

.nav-item.active .nav-dot {
  background: #93b4ff;
  opacity: 1;
}

.nav-label {
  flex: 1;
  min-width: 0;
}
</style>
