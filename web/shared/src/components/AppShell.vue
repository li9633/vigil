<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { FontAwesomeIcon } from '@fortawesome/vue-fontawesome'
import { faBars, faXmark } from '../icons'
import ThemeToggle from './ThemeToggle.vue'

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
const scrolled = ref(false)

function onScroll() {
  scrolled.value = window.scrollY > 4
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') mobileOpen.value = false
}

onMounted(() => {
  window.addEventListener('scroll', onScroll, { passive: true })
  window.addEventListener('keydown', onKeydown)
  onScroll()
})

onBeforeUnmount(() => {
  window.removeEventListener('scroll', onScroll)
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="shell" :class="{ 'mobile-open': mobileOpen }">
    <a class="skip-link" href="#main-content">跳到主内容</a>

    <aside class="sidebar">
      <div class="sidebar-brand">
        <slot name="brand" />
      </div>
      <nav class="sidebar-nav" aria-label="主导航" @click="mobileOpen = false">
        <slot name="nav" />
      </nav>
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
      <header class="topbar" :class="{ 'is-scrolled': scrolled }">
        <button
          class="menu-btn"
          type="button"
          aria-label="打开导航"
          :aria-expanded="mobileOpen"
          @click="mobileOpen = true"
        >
          <FontAwesomeIcon :icon="faBars" fixed-width />
        </button>
        <div class="title-block">
          <h1 class="page-heading">{{ title }}</h1>
          <p v-if="subtitle" class="page-subheading">{{ subtitle }}</p>
        </div>
        <div class="actions">
          <slot name="status" />
          <ThemeToggle />
          <slot name="actions" />
        </div>
      </header>

      <main id="main-content" class="content" tabindex="-1">
        <slot />
      </main>
    </div>

    <button
      v-if="mobileOpen"
      class="drawer-close"
      type="button"
      aria-label="关闭导航"
      @click="mobileOpen = false"
    >
      <FontAwesomeIcon :icon="faXmark" fixed-width />
    </button>
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
  border-right: 1px solid var(--vigil-color-border);
  color: var(--vigil-color-sidebar-text);
}

.sidebar-brand {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  min-height: 72px;
  padding: 0 var(--vigil-space-5);
  border-bottom: 1px solid var(--vigil-color-border);
}

.sidebar-nav {
  flex: 1;
  min-height: 0;
  padding: var(--vigil-space-3) var(--vigil-space-2);
  overflow-y: auto;
}

.sidebar-foot {
  padding: var(--vigil-space-3) var(--vigil-space-5);
  border-top: 1px solid var(--vigil-color-border);
  font-size: var(--vigil-font-xxs);
  letter-spacing: 0.04em;
  color: var(--vigil-color-text-muted);
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
  padding: 0 var(--vigil-space-6);
  background: var(--vigil-color-topbar, var(--vigil-color-surface));
  border-bottom: 1px solid var(--vigil-color-border);
  transition: box-shadow var(--vigil-transition), border-color var(--vigil-transition);

  &.is-scrolled {
    box-shadow: var(--vigil-shadow-sm);
  }
}

@supports (backdrop-filter: blur(12px)) {
  .topbar {
    backdrop-filter: blur(12px);
  }
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
  padding: 28px;

  &:focus {
    outline: none;
  }
}

.menu-btn,
.drawer-close {
  display: none;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: 1px solid var(--vigil-color-border);
  border-radius: var(--vigil-radius-sm);
  background: var(--vigil-color-surface);
  color: var(--vigil-color-text);
  font-size: 15px;
  cursor: pointer;
}

.scrim {
  display: none;
}

@media (max-width: 900px) {
  .sidebar {
    position: fixed;
    inset: 0 auto 0 0;
    z-index: 50;
    transform: translateX(-100%);
    transition: transform 240ms var(--vigil-ease-out);
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
    z-index: 45;
    display: block;
    border: none;
    background: var(--vigil-color-overlay);
  }

  .drawer-close {
    position: fixed;
    top: 16px;
    right: 16px;
    z-index: 55;
    display: inline-flex;
  }

  .topbar {
    padding: 0 var(--vigil-space-4);
  }

  .content {
    padding: var(--vigil-space-5);
  }
}

@media (max-width: 640px) {
  .content {
    padding: var(--vigil-space-4);
  }
}

@media (max-width: 420px) {
  .content {
    padding: var(--vigil-space-3);
  }
}
</style>
