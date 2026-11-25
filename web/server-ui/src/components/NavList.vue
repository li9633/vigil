<script setup lang="ts">
import { useRoute } from 'vue-router'

interface NavItem {
  to: string
  label: string
  milestone?: string
}

const items: NavItem[] = [
  { to: '/', label: '总览' },
  { to: '/clients', label: '客户端' },
  { to: '/incidents', label: '告警中心' },
  { to: '/notifications', label: '通知配置', milestone: 'M2' },
  { to: '/settings', label: '策略配置', milestone: 'M3' },
  { to: '/audit', label: '审计日志', milestone: 'M3' },
]

const route = useRoute()

function isActive(to: string): boolean {
  return to === '/' ? route.path === '/' : route.path === to || route.path.startsWith(`${to}/`)
}
</script>

<template>
  <nav class="nav-list" aria-label="主导航">
    <router-link
      v-for="item in items"
      :key="item.to"
      :to="item.to"
      class="nav-item"
      :class="{ active: isActive(item.to) }"
    >
      <span class="nav-dot" aria-hidden="true"></span>
      <span class="nav-label">{{ item.label }}</span>
      <span v-if="item.milestone" class="nav-badge">{{ item.milestone }}</span>
    </router-link>
  </nav>
</template>

<style scoped lang="scss">
.nav-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--vigil-space-2) var(--vigil-space-2);
}

.nav-item {
  position: relative;
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
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  opacity: 0.45;
  flex: 0 0 auto;
}

.nav-item.active .nav-dot {
  opacity: 1;
  background: #93b4ff;
}

.nav-label {
  flex: 1;
  min-width: 0;
}

.nav-badge {
  padding: 0 5px;
  border-radius: var(--vigil-radius-sm);
  border: 1px solid rgba(255, 255, 255, 0.24);
  font-size: 10px;
  line-height: 16px;
  color: rgba(255, 255, 255, 0.72);
}
</style>
