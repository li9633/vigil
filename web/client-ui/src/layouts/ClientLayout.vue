<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { FontAwesomeIcon } from '@fortawesome/vue-fontawesome'
import { AppShell, faRightFromBracket } from '@vigil/shared'
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
      <button class="logout-btn" type="button" aria-label="退出登录" @click="logout">
        <FontAwesomeIcon :icon="faRightFromBracket" fixed-width />
        <span>退出</span>
      </button>
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
  width: 32px;
  height: 32px;
  border-radius: var(--vigil-radius-sm);
  background: var(--vigil-gradient-brand);
  color: var(--vigil-color-on-primary);
  font-size: var(--vigil-font-md);
  font-weight: 700;
  box-shadow: var(--vigil-shadow-glow);
}

.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.25;

  strong {
    font-size: var(--vigil-font-md);
    letter-spacing: 0.06em;
    color: var(--vigil-color-text);
  }

  small {
    font-size: var(--vigil-font-xxs);
    color: var(--vigil-color-text-muted);
  }
}

.logout-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 36px;
  padding: 4px 12px;
  border: 1px solid var(--vigil-color-border);
  border-radius: var(--vigil-radius-sm);
  background: var(--vigil-color-surface);
  color: var(--vigil-color-text-secondary);
  font-size: var(--vigil-font-xs);
  cursor: pointer;
  transition: background var(--vigil-transition), color var(--vigil-transition),
    border-color var(--vigil-transition);

  &:hover {
    color: var(--vigil-color-primary);
    border-color: var(--vigil-color-primary);
    background: var(--vigil-color-surface-alt);
  }
}

@media (max-width: 480px) {
  .logout-btn span {
    display: none;
  }
}

:deep(.content) {
  max-width: 1080px;
}
</style>
