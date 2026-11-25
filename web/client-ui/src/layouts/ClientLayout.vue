<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { AppShell } from '@vigil/shared'
import ClientNav from '@/components/ClientNav.vue'
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
  <AppShell :title="pageTitle" subtitle="本地检测与自愈" footer="Vigil v1 · 客户端">
    <template #brand>
      <div class="brand">
        <span class="brand-mark" aria-hidden="true">V</span>
        <span class="brand-text">
          <strong>Vigil</strong>
          <small>OpenWrt 客户端</small>
        </span>
      </div>
    </template>

    <template #nav>
      <ClientNav />
    </template>

    <template #actions>
      <button class="logout-btn" type="button" @click="logout">退出</button>
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
    letter-spacing: 0.06em;
  }

  small {
    font-size: 11px;
    color: rgba(199, 210, 223, 0.72);
  }
}

.logout-btn {
  border: 1px solid var(--vigil-color-border);
  border-radius: var(--vigil-radius-sm);
  background: var(--vigil-color-surface);
  color: var(--vigil-color-text-secondary);
  padding: 4px 12px;
  font-size: var(--vigil-font-xs);
  cursor: pointer;
  transition: background var(--vigil-transition), color var(--vigil-transition);

  &:hover {
    color: var(--vigil-color-text);
    background: var(--vigil-color-surface-alt);
  }
}

:deep(.content) {
  max-width: 1080px;
}
</style>
