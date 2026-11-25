<script setup lang="ts">
import { computed } from 'vue'
import { FontAwesomeIcon } from '@fortawesome/vue-fontawesome'
import { faRotateRight } from '../icons'

const props = withDefaults(
  defineProps<{
    loading?: boolean
    paused?: boolean
    error?: boolean
    disabled?: boolean
  }>(),
  { loading: false, paused: false, error: false, disabled: false },
)

const emit = defineEmits<{ refresh: [] }>()

const showBadge = computed(() => props.paused || props.error)

const tip = computed(() => {
  if (props.paused) return '自动更新已暂停，点击重试'
  if (props.loading) return '正在刷新…'
  if (props.error) return '刷新失败，点击重试'
  return '刷新数据'
})

function onClick() {
  if (props.loading || props.disabled) return
  emit('refresh')
}
</script>

<template>
  <button
    class="refresh-fab"
    :class="{ 'is-busy': loading, 'is-paused': showBadge }"
    type="button"
    :aria-busy="loading"
    :aria-label="tip"
    :disabled="loading || disabled"
    @click="onClick"
  >
    <FontAwesomeIcon :icon="faRotateRight" fixed-width class="fab-icon" />
    <span v-if="showBadge" class="fab-badge" aria-hidden="true"></span>
    <span class="fab-tip" role="tooltip">{{ tip }}</span>
  </button>
</template>

<style scoped lang="scss">
.refresh-fab {
  position: fixed;
  right: 24px;
  bottom: 24px;
  z-index: 40;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border: 1px solid var(--vigil-color-border);
  border-radius: 50%;
  background: var(--vigil-color-surface-raised);
  color: var(--vigil-color-text-secondary);
  box-shadow: var(--vigil-shadow-md);
  font-size: 16px;
  cursor: pointer;
  transition: color var(--vigil-transition), border-color var(--vigil-transition),
    box-shadow var(--vigil-transition), transform var(--vigil-transition);

  &:hover:not(:disabled) {
    color: var(--vigil-color-primary);
    border-color: var(--vigil-color-primary);
    box-shadow: var(--vigil-shadow-glow);
    transform: translateY(-1px);
  }

  &:active:not(:disabled) {
    transform: scale(0.96);
  }

  &:disabled {
    cursor: progress;
  }

  &.is-paused {
    color: var(--vigil-color-warn);
    border-color: var(--vigil-color-warn);
  }
}

.fab-icon {
  line-height: 1;
}

.is-busy .fab-icon {
  animation: fab-rotate 0.9s linear infinite;
}

.fab-badge {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--vigil-color-critical);
  box-shadow: 0 0 0 2px var(--vigil-color-surface-raised);
}

.fab-tip {
  position: absolute;
  right: 0;
  bottom: calc(100% + 8px);
  padding: 4px 8px;
  border: 1px solid var(--vigil-color-border);
  border-radius: var(--vigil-radius-sm);
  background: var(--vigil-color-surface-raised);
  box-shadow: var(--vigil-shadow-md);
  color: var(--vigil-color-text);
  font-size: var(--vigil-font-xs);
  line-height: 1.4;
  white-space: nowrap;
  opacity: 0;
  transform: translateY(4px);
  pointer-events: none;
  transition: opacity var(--vigil-transition), transform var(--vigil-transition);
  transition-delay: 0ms;
}

.refresh-fab:hover .fab-tip,
.refresh-fab:focus-visible .fab-tip {
  opacity: 1;
  transform: translateY(0);
  transition-delay: 600ms;
}

@keyframes fab-rotate {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 640px) {
  .refresh-fab {
    right: 16px;
    bottom: calc(16px + env(safe-area-inset-bottom, 0px));
  }
}

@media (prefers-reduced-motion: reduce) {
  .is-busy .fab-icon {
    animation: fab-breathe 1.4s ease-in-out infinite;
  }

  .refresh-fab:hover {
    transform: none;
  }
}

@keyframes fab-breathe {
  50% {
    opacity: 0.45;
  }
}
</style>
