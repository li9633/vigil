<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { AppShell } from '@vigil/shared'
import NavList from '@/components/NavList.vue'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const pageTitle = computed(() => (route.meta.title as string | undefined) ?? 'Vigil')

function logout() {
  auth.signOut()
  router.replace({ name: 'login' })
}
</script>

<template>
  <AppShell
    :title="pageTitle"
    subtitle="服务端管理台 · 监控与告警"
    footer="Vigil v1 · 服务端"
  >
    <template #brand>
      <div class="brand">
        <span class="brand-mark" aria-hidden="true">V</span>
        <span class="brand-text">
          <strong>Vigil</strong>
          <small>服务端管理台</small>
        </span>
      </div>
    </template>

    <template #nav>
      <NavList />
    </template>

    <template #actions>
      <span class="user-name">{{ auth.username || '管理员' }}</span>
      <el-button size="small" text @click="logout">退出登录</el-button>
    </template>

    <router-view />
  </AppShell>
</template>

<style scoped lang="scss">
.brand {
  display: flex;
  align-items: center;
  gap: var(--vigil-space-2);
}

.brand-mark {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: var(--vigil-radius-sm);
  background: linear-gradient(135deg, #2563eb, #38bdf8);
  color: #fff;
  font-size: var(--vigil-font-md);
  font-weight: 700;
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

.user-name {
  font-size: var(--vigil-font-sm);
  color: var(--vigil-color-text-secondary);
}

@media (max-width: 480px) {
  .user-name {
    display: none;
  }
}
</style>
