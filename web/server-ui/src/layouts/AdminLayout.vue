<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import NavList from '@/components/NavList.vue'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const drawerOpen = ref(false)

const pageTitle = computed(() => (route.meta.title as string | undefined) ?? 'Vigil')

watch(
  () => route.fullPath,
  () => {
    drawerOpen.value = false
  },
)

function logout() {
  auth.signOut()
  router.replace({ name: 'login' })
}
</script>

<template>
  <div class="admin-shell">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true">V</span>
        <span class="brand-text">
          <strong>Vigil</strong>
          <small>服务端管理台</small>
        </span>
      </div>
      <NavList />
      <div class="sidebar-foot">v1 · M3 页面骨架</div>
    </aside>

    <div class="main">
      <header class="topbar">
        <button class="menu-btn" type="button" aria-label="打开导航" @click="drawerOpen = true">
          ☰
        </button>
        <h1 class="topbar-title">{{ pageTitle }}</h1>
        <div class="topbar-right">
          <span class="user-name">{{ auth.username || '管理员' }}</span>
          <el-button size="small" text @click="logout">退出登录</el-button>
        </div>
      </header>

      <main class="content">
        <router-view />
      </main>
    </div>

    <el-drawer v-model="drawerOpen" direction="ltr" size="240px" :with-header="false">
      <div class="drawer-inner">
        <div class="brand">
          <span class="brand-mark" aria-hidden="true">V</span>
          <span class="brand-text">
            <strong>Vigil</strong>
            <small>服务端管理台</small>
          </span>
        </div>
        <NavList />
      </div>
    </el-drawer>
  </div>
</template>

<style scoped lang="scss">
.admin-shell {
  display: flex;
  min-height: 100vh;
  background: var(--vigil-color-bg);
}

.sidebar {
  position: sticky;
  top: 0;
  display: flex;
  flex-direction: column;
  flex: 0 0 var(--vigil-sidebar-width);
  width: var(--vigil-sidebar-width);
  height: 100vh;
  background: var(--vigil-color-sidebar-bg);
  color: var(--vigil-color-sidebar-text);
}

.brand {
  display: flex;
  align-items: center;
  gap: var(--vigil-space-2);
  padding: var(--vigil-space-4);
  color: #fff;
}

.brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: var(--vigil-radius-sm);
  background: linear-gradient(135deg, #2563eb, #38bdf8);
  font-weight: 700;
  font-size: var(--vigil-font-md);
  color: #fff;
  flex: 0 0 auto;
}

.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.25;

  strong {
    font-size: var(--vigil-font-md);
    letter-spacing: 0.04em;
  }

  small {
    font-size: 11px;
    color: rgba(199, 210, 223, 0.72);
  }
}

.sidebar-foot {
  margin-top: auto;
  padding: var(--vigil-space-3) var(--vigil-space-4);
  font-size: 11px;
  color: rgba(199, 210, 223, 0.5);
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}

.main {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  gap: var(--vigil-space-3);
  height: var(--vigil-header-height);
  padding: 0 var(--vigil-space-5);
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(6px);
  border-bottom: 1px solid var(--vigil-color-border);
}

.topbar-title {
  font-size: var(--vigil-font-md);
  font-weight: 600;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: var(--vigil-space-2);
}

.user-name {
  font-size: var(--vigil-font-sm);
  color: var(--vigil-color-text-secondary);
}

.menu-btn {
  display: none;
  border: 1px solid var(--vigil-color-border);
  background: var(--vigil-color-surface);
  border-radius: var(--vigil-radius-sm);
  width: 32px;
  height: 32px;
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
  color: var(--vigil-color-text);
}

.content {
  flex: 1;
  width: 100%;
  max-width: var(--vigil-content-max);
  margin: 0 auto;
  padding: var(--vigil-space-5);
}

.drawer-inner {
  background: var(--vigil-color-sidebar-bg);
  color: var(--vigil-color-sidebar-text);
  min-height: 100vh;
  margin: -20px;
  padding: 20px 0;
}

:deep(.el-drawer__body) {
  padding: 0;
  background: var(--vigil-color-sidebar-bg);
}

@media (max-width: 900px) {
  .sidebar {
    display: none;
  }

  .menu-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
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

  .user-name {
    display: none;
  }
}
</style>
