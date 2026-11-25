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
    <div class="empty-icon" aria-hidden="true">○</div>
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
  width: 40px;
  height: 40px;
  border-radius: var(--vigil-radius-pill);
  border: 1px dashed var(--vigil-color-border-strong);
  font-size: 22px;
  line-height: 1;
  color: var(--vigil-color-text-muted);
}

.empty-title {
  margin: 0;
  font-size: var(--vigil-font-base);
  font-weight: 600;
  color: var(--vigil-color-text-secondary);
}

.empty-desc {
  margin: 0;
  font-size: var(--vigil-font-xs);
  max-width: 420px;
  line-height: 1.6;
}

.empty-action {
  margin-top: var(--vigil-space-2);
}
</style>
