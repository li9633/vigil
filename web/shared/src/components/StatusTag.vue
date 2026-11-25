<script setup lang="ts">
import { computed } from 'vue'
import { normalizeStatus, statusLabel } from '../status'

const props = withDefaults(
  defineProps<{
    status: string | null | undefined
    label?: string
    size?: 'sm' | 'md'
  }>(),
  { label: '', size: 'md' },
)

const tone = computed(() => normalizeStatus(props.status))
const text = computed(() => props.label || statusLabel(props.status))
</script>

<template>
  <span class="status-tag" :class="[`tone-${tone}`, `size-${size}`]" role="status">
    <span class="dot" aria-hidden="true"></span>
    <span class="text">{{ text }}</span>
  </span>
</template>

<style scoped lang="scss">
.status-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  padding: 1px 8px;
  border-radius: var(--vigil-radius-pill);
  font-size: var(--vigil-font-xs);
  font-weight: 500;
  line-height: 20px;
  white-space: nowrap;
  border: 1px solid transparent;

  .dot {
    flex: 0 0 auto;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: currentColor;
  }

  .text {
    overflow: hidden;
    text-overflow: ellipsis;
  }

  &.size-sm {
    padding: 0 6px;
    font-size: 11px;
    line-height: 18px;
  }

  &.size-md {
    font-size: var(--vigil-font-xs);
  }
}

.tone-ok {
  color: var(--vigil-color-ok);
  background: var(--vigil-color-ok-weak);
  border-color: rgba(22, 163, 74, 0.18);
}

.tone-warn {
  color: var(--vigil-color-warn);
  background: var(--vigil-color-warn-weak);
  border-color: rgba(217, 119, 6, 0.2);
}

.tone-critical {
  color: var(--vigil-color-critical);
  background: var(--vigil-color-critical-weak);
  border-color: rgba(220, 38, 38, 0.2);
}

.tone-unknown {
  color: var(--vigil-color-unknown);
  background: var(--vigil-color-unknown-weak);
  border-color: rgba(107, 114, 128, 0.2);
}
</style>
