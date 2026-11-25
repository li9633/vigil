<script setup lang="ts">
withDefaults(
  defineProps<{
    text?: string
    minHeight?: string
    skeleton?: boolean
    rows?: number
  }>(),
  { text: '加载中…', minHeight: '120px', skeleton: false, rows: 3 },
)
</script>

<template>
  <div
    class="loading-block"
    :class="{ 'is-skeleton': skeleton }"
    :style="{ minHeight }"
    role="status"
    aria-live="polite"
  >
    <template v-if="skeleton">
      <span v-for="row in rows" :key="row" class="skeleton-row" aria-hidden="true"></span>
      <span class="visually-hidden">{{ text }}</span>
    </template>
    <template v-else>
      <span class="spinner" aria-hidden="true"></span>
      <span class="loading-text">{{ text }}</span>
    </template>
  </div>
</template>

<style scoped lang="scss">
.loading-block {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--vigil-space-3);
  color: var(--vigil-color-text-muted);
  font-size: var(--vigil-font-sm);

  &.is-skeleton {
    flex-direction: column;
    align-items: stretch;
    justify-content: flex-start;
    gap: var(--vigil-space-3);
    padding: var(--vigil-space-4) 0;
  }
}

.spinner {
  width: 18px;
  height: 18px;
  border: 2px solid var(--vigil-color-border-strong);
  border-top-color: var(--vigil-color-primary);
  border-radius: 50%;
  animation: vigil-spin 0.8s linear infinite;
}

.loading-text {
  letter-spacing: 0.02em;
}

.skeleton-row {
  display: block;
  height: 16px;
  border-radius: var(--vigil-radius-sm);
  background: linear-gradient(
    90deg,
    var(--vigil-color-surface-alt) 0%,
    var(--vigil-color-border) 50%,
    var(--vigil-color-surface-alt) 100%
  );
  background-size: 200% 100%;
  animation: skeleton-shimmer 1.4s linear infinite;

  &:nth-child(2n) {
    width: 82%;
  }

  &:last-of-type {
    width: 56%;
  }
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}

@keyframes skeleton-shimmer {
  from {
    background-position: 200% 0;
  }
  to {
    background-position: -200% 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .skeleton-row {
    animation: none;
  }
}
</style>
