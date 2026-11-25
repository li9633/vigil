<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    label: string
    value: string | number
    hint?: string
    tone?: 'ok' | 'warn' | 'critical' | 'unknown' | ''
    /** 关键指标：顶部渐变发丝线 */
    accent?: boolean
    /** 故障态：右上角呼吸圆点 */
    pulse?: boolean
  }>(),
  { hint: '', tone: '', accent: false, pulse: false },
)

const toneClass = computed(() => (props.tone ? `tone-${props.tone}` : ''))
</script>

<template>
  <div class="stat-card-v2" :class="[toneClass, { accent, pulse }]">
    <span class="stat-card-label">{{ label }}</span>
    <strong class="stat-card-value">{{ value }}</strong>
    <span v-if="hint" class="stat-card-hint">{{ hint }}</span>
    <span v-if="pulse" class="stat-card-dot" aria-hidden="true"></span>
  </div>
</template>

<style scoped lang="scss">
.stat-card-v2 {
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: var(--vigil-space-1);
  min-height: 112px;
  padding: var(--vigil-space-4);
  overflow: hidden;
  background: var(--vigil-color-surface);
  border: 1px solid var(--vigil-color-border);
  border-radius: var(--vigil-radius-md);
  box-shadow: var(--vigil-shadow-sm);

  &.accent::before {
    content: '';
    position: absolute;
    inset: 0 0 auto;
    height: 2px;
    background: var(--vigil-gradient-brand);
  }
}

.stat-card-label {
  font-size: var(--vigil-font-xs);
  color: var(--vigil-color-text-muted);
}

.stat-card-value {
  font-family: var(--vigil-font-mono);
  font-size: var(--vigil-font-xl);
  font-weight: 650;
  line-height: 1.15;
  font-variant-numeric: tabular-nums;
}

.stat-card-hint {
  font-size: var(--vigil-font-xxs);
  color: var(--vigil-color-text-muted);
}

.tone-ok .stat-card-value {
  color: var(--vigil-color-ok);
}

.tone-warn .stat-card-value {
  color: var(--vigil-color-warn);
}

.tone-critical .stat-card-value {
  color: var(--vigil-color-critical);
}

.tone-unknown .stat-card-value {
  color: var(--vigil-color-unknown);
}

.stat-card-dot {
  position: absolute;
  top: var(--vigil-space-3);
  right: var(--vigil-space-3);
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--vigil-color-critical);
  color: var(--vigil-color-critical);
  animation: stat-pulse 2s ease-in-out infinite;
}

@keyframes stat-pulse {
  0%,
  100% {
    opacity: 0.45;
  }
  50% {
    opacity: 1;
  }
}

@media (max-width: 640px) {
  .stat-card-v2 {
    min-height: 96px;
  }

  .stat-card-value {
    font-size: var(--vigil-font-lg);
  }
}
</style>
