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
const longText = computed(() => text.value.length > 6)
</script>

<template>
  <span
    class="status-tag"
    :class="[`tone-${tone}`, `size-${size}`]"
    role="status"
    :title="longText ? text : undefined"
  >
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
  padding: 0 9px;
  border: 1px solid transparent;
  border-radius: var(--vigil-radius-pill);
  font-size: var(--vigil-font-xs);
  font-weight: 500;
  line-height: 20px;
  white-space: nowrap;

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
    gap: 5px;
    padding: 0 7px;
    font-size: var(--vigil-font-xxs);
    line-height: 18px;
  }
}

.tone-ok {
  color: var(--vigil-color-ok);
  background: var(--vigil-color-ok-weak);
}

.tone-warn {
  color: var(--vigil-color-warn);
  background: var(--vigil-color-warn-weak);
}

.tone-critical {
  color: var(--vigil-color-critical);
  background: var(--vigil-color-critical-weak);
}

.tone-unknown {
  color: var(--vigil-color-unknown);
  background: var(--vigil-color-unknown-weak);
  border-style: dashed;
  border-color: var(--vigil-color-border-strong);
}

// 故障圆点呼吸，仅一处脉冲
.tone-critical .dot {
  animation: status-breathe 2s ease-in-out infinite;
}

@supports (color: color-mix(in srgb, currentColor 18%, transparent)) {
  .tone-ok {
    border-color: color-mix(in srgb, currentColor 18%, transparent);
  }

  .tone-warn {
    border-color: color-mix(in srgb, currentColor 16%, transparent);
  }

  .tone-critical {
    border-color: color-mix(in srgb, currentColor 22%, transparent);
  }
}

@keyframes status-breathe {
  0%,
  100% {
    opacity: 0.4;
  }
  50% {
    opacity: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .tone-critical .dot {
    animation: none;
  }
}
</style>
