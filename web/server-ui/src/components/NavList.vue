<script setup lang="ts">
import { useRoute } from 'vue-router'
import { FontAwesomeIcon } from '@fortawesome/vue-fontawesome'
import { navIcons } from '@vigil/shared'

interface NavItem {
  to: string
  label: string
}

interface NavGroup {
  title: string
  items: NavItem[]
}

const groups: NavGroup[] = [
  {
    title: '监控',
    items: [
      { to: '/', label: '总览' },
      { to: '/clients', label: '客户端' },
      { to: '/incidents', label: '告警中心' },
    ],
  },
  {
    title: '配置',
    items: [
      { to: '/notifications', label: '通知配置' },
      { to: '/settings', label: '策略配置' },
      { to: '/audit', label: '审计日志' },
    ],
  },
  {
    title: '系统',
    items: [
      { to: '/system', label: '系统设置' },
      { to: '/logs', label: '服务端日志' },
    ],
  },
]

const route = useRoute()

function isActive(to: string): boolean {
  return to === '/' ? route.path === '/' : route.path === to || route.path.startsWith(`${to}/`)
}
</script>

<template>
  <div class="nav-list">
    <template v-for="group in groups" :key="group.title">
      <p class="nav-group">{{ group.title }}</p>
      <router-link
        v-for="item in group.items"
        :key="item.to"
        :to="item.to"
        class="nav-item"
        :class="{ active: isActive(item.to) }"
      >
        <FontAwesomeIcon
          class="nav-icon"
          :icon="navIcons[item.label]"
          fixed-width
          aria-hidden="true"
        />
        <span class="nav-label">{{ item.label }}</span>
      </router-link>
    </template>
  </div>
</template>

<style scoped lang="scss">
.nav-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav-group {
  padding: var(--vigil-space-4) var(--vigil-space-3) var(--vigil-space-1);
  font-size: var(--vigil-font-xxs);
  font-weight: 500;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--vigil-color-text-muted);
}

.nav-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 40px;
  padding: 0 var(--vigil-space-3);
  border-radius: var(--vigil-radius-sm);
  color: var(--vigil-color-sidebar-text);
  font-size: var(--vigil-font-sm);
  transition: background var(--vigil-transition), color var(--vigil-transition);

  &:hover {
    background: var(--vigil-color-sidebar-hover);
    color: var(--vigil-color-text);
  }

  &.active {
    background: var(--vigil-color-sidebar-active);
    color: var(--vigil-color-text);
    font-weight: 600;

    &::before {
      content: '';
      position: absolute;
      top: 8px;
      bottom: 8px;
      left: 0;
      width: 3px;
      border-radius: 2px;
      background: var(--vigil-gradient-brand);
    }
  }
}

.nav-icon {
  flex: 0 0 auto;
  width: 18px;
  font-size: 16px;
  text-align: center;
  opacity: 0.75;
}

.nav-item.active .nav-icon {
  color: var(--vigil-color-primary);
  opacity: 1;
}

.nav-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 900px) {
  .nav-item {
    min-height: 44px;
  }
}
</style>
