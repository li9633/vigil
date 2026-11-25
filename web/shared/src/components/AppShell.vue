<script setup lang="ts">
import { ref } from 'vue'

withDefaults(
  defineProps<{
    title: string
    subtitle?: string
    footer?: string
  }>(),
  {
    subtitle: '',
    footer: '',
  },
)

const mobileOpen = ref(false)
</script>

<template>
  <div class="shell" :class="{ 'mobile-open': mobileOpen }">
    <aside class="sidebar">
      <div class="sidebar-brand">
        <slot name="brand" />
      </div>
      <div class="sidebar-nav" @click="mobileOpen = false">
        <slot name="nav" />
      </div>
      <div v-if="footer || $slots.footer" class="sidebar-foot">
        <slot name="footer">{{ footer }}</slot>
      </div>
    </aside>

    <button
      v-if="mobileOpen"
      class="scrim"
      type="button"
      aria-label="关闭导航"
      @click="mobileOpen = false"
    ></button>

    <div class="main">
      <header class="topbar">
        <button
          class="menu-btn"
          type="button"
          aria-label="打开导航"
          :aria-expanded="mobileOpen"
          @click="mobileOpen = true"
        >
          ☰
        </button>
        <div class="title-block">
          <h1 class="page-heading">{{ title }}</h1>
          <p v-if="subtitle" class="page-subheading">{{ subtitle }}</p>
        </div>
        <div class="actions">
          <slot name="actions" />
        </div>
      </header>

      <main class="content">
        <slot />
      </main>
    </div>
  </div>
</template>

<style scoped lang="scss">
.shell {
  display: flex;
  min-height: 100vh;
  background: var(--vigil-color-bg);
}

.sidebar {
  position: sticky;
  top: 0;
  z-index: 30;
  display: flex;
  flex: 0 0 var(--vigil-sidebar-width);
  flex-direction: column;
  width: var(--vigil-sidebar-width);
  height: 100vh;
  background: var(--vigil-color-sidebar-bg);
  color: var(--vigil-color-sidebar-text);
}

.sidebar-brand {
  padding: var(--vigil-space-4);
  color: #fff;
}

.sidebar-nav {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.sidebar-foot {
  padding: var(--vigil-space-3) var(--vigil-space-4);
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  font-size: 11px;
  color: rgba(199, 210, 223, 0.5);
}

.main {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  gap: var(--vigil-space-3);
  height: var(--vigil-header-height);
  padding: 0 var(--vigil-space-5);
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(6px);
  border-bottom: 1px solid var(--vigil-color-border);
}

.title-block {
  min-width: 0;
}

.page-heading {
  overflow: hidden;
  font-size: var(--vigil-font-md);
  font-weight: 600;
  line-height: 1.3;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.page-subheading {
  margin: 2px 0 0;
  overflow: hidden;
  font-size: var(--vigil-font-xs);
  color: var(--vigil-color-text-muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.actions {
  display: flex;
  align-items: center;
  gap: var(--vigil-space-2);
  margin-left: auto;
}

.content {
  flex: 1;
  width: 100%;
  max-width: var(--vigil-content-max);
  margin: 0 auto;
  padding: var(--vigil-space-5);
}

.menu-btn {
  display: none;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: 1px solid var(--vigil-color-border);
  border-radius: var(--vigil-radius-sm);
  background: var(--vigil-color-surface);
  color: var(--vigil-color-text);
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
}

.scrim {
  display: none;
}

@media (max-width: 900px) {
  .sidebar {
    position: fixed;
    inset: 0 auto 0 0;
    transform: translateX(-100%);
    transition: transform var(--vigil-transition);
  }

  .mobile-open .sidebar {
    transform: translateX(0);
  }

  .menu-btn {
    display: inline-flex;
  }

  .scrim {
    position: fixed;
    inset: 0;
    z-index: 25;
    display: block;
    border: none;
    background: rgba(17, 28, 46, 0.42);
  }

  .topbar {
    padding: 0 var(--vigil-space-4);
  }

  .content {
    padding: var(--vigil-space-4);
  }
}

@media (max-width: 480px) {
  .content {
    padding: var(--vigil-space-3);
  }
}
</style>
