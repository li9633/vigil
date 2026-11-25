<script setup lang="ts">
withDefaults(
  defineProps<{
    text?: string
    minHeight?: string
  }>(),
  { text: '加载中…', minHeight: '120px' },
)
</script>

<template>
  <div class="loading-block" :style="{ minHeight }" role="status" aria-live="polite">
    <span class="spinner" aria-hidden="true"></span>
    <span class="loading-text">{{ text }}</span>
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

@keyframes vigil-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
