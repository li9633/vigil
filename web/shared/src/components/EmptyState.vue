<script setup lang="ts">
withDefaults(
  defineProps<{
    title?: string
    description?: string
    compact?: boolean
  }>(),
  { title: '暂无数据', description: '', compact: false },
)
</script>

<template>
  <div class="empty-state" :class="{ compact }">
    <div class="empty-icon" aria-hidden="true">
      <svg viewBox="0 0 48 48" fill="none">
        <circle cx="15" cy="16" r="2.4" fill="currentColor" />
        <circle cx="34" cy="13" r="1.6" fill="currentColor" opacity=".7" />
        <circle cx="26" cy="31" r="2" fill="currentColor" opacity=".85" />
        <circle cx="37" cy="34" r="1.4" fill="currentColor" opacity=".55" />
        <path
          d="M15 16 34 13M15 16l11 15m8-18-8 18m11 3-9-3"
          stroke="currentColor"
          stroke-width="1"
          stroke-linecap="round"
          opacity=".45"
        />
      </svg>
    </div>
    <p class="empty-title">{{ title }}</p>
    <p v-if="description" class="empty-desc">{{ description }}</p>
    <div v-if="$slots.action" class="empty-action">
      <slot name="action" />
    </div>
  </div>
</template>

<style scoped lang="scss">
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--vigil-space-2);
  padding: var(--vigil-space-8) var(--vigil-space-4);
  text-align: center;
  color: var(--vigil-color-text-muted);

  &.compact {
    padding: var(--vigil-space-5) var(--vigil-space-3);
  }
}

.empty-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  margin-bottom: var(--vigil-space-1);
  border-radius: var(--vigil-radius-lg);
  background: var(--vigil-color-surface-alt);
  color: var(--vigil-color-primary);

  svg {
    width: 32px;
    height: 32px;
  }
}

.empty-title {
  margin: 0;
  font-size: var(--vigil-font-base);
  font-weight: 600;
  color: var(--vigil-color-text-secondary);
}

.empty-desc {
  max-width: 420px;
  font-size: var(--vigil-font-xs);
  line-height: 1.6;
}

.empty-action {
  margin-top: var(--vigil-space-2);
}
</style>
