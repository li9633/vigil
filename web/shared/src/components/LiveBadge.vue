<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const props = withDefaults(
  defineProps<{
    at?: Date | string | null
    paused?: boolean
  }>(),
  { at: null, paused: false },
)

const now = ref(Date.now())
let timer: number | undefined

onMounted(() => {
  timer = window.setInterval(() => {
    now.value = Date.now()
  }, 1000)
})

onBeforeUnmount(() => {
  if (timer !== undefined) window.clearInterval(timer)
})

const seconds = computed(() => {
  if (!props.at) return null
  const ts = props.at instanceof Date ? props.at.getTime() : new Date(props.at).getTime()
  if (Number.isNaN(ts)) return null
  return Math.max(0, Math.round((now.value - ts) / 1000))
})

const text = computed(() => {
  if (props.paused) return '自动更新已暂停'
  if (seconds.value === null) return '等待首次数据'
  if (seconds.value < 5) return '刚刚更新'
  if (seconds.value < 60) return `${seconds.value} 秒前更新`
  const minutes = Math.floor(seconds.value / 60)
  if (minutes < 60) return `${minutes} 分钟前更新`
  return `${Math.floor(minutes / 60)} 小时前更新`
})
</script>

<template>
  <span class="live-badge" :class="{ paused, 'is-live': !paused }" role="status">
    <span class="live-dot" aria-hidden="true"></span>
    <span class="live-label">LIVE</span>
    <span class="live-text">{{ text }}</span>
  </span>
</template>

<style scoped lang="scss">
.live-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 10px 2px 8px;
  border: 1px solid var(--vigil-color-border);
  border-radius: var(--vigil-radius-pill);
  background: var(--vigil-color-surface);
  color: var(--vigil-color-text-muted);
  font-size: var(--vigil-font-xxs);
  line-height: 20px;
  white-space: nowrap;
}

.live-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--vigil-color-accent);
  color: var(--vigil-color-accent);
}

.is-live .live-dot {
  animation: live-pulse 1.6s ease-out infinite;
}

.live-label {
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--vigil-color-accent);
}

.live-text {
  color: var(--vigil-color-text-muted);
}

.live-badge.paused {
  border-color: var(--vigil-color-warn);

  .live-dot {
    background: var(--vigil-color-warn);
    color: var(--vigil-color-warn);
    animation: none;
  }

  .live-label,
  .live-text {
    color: var(--vigil-color-warn);
  }
}

@keyframes live-pulse {
  0% {
    box-shadow: 0 0 0 0 currentColor;
  }
  70% {
    box-shadow: 0 0 0 5px transparent;
  }
  100% {
    box-shadow: 0 0 0 0 transparent;
  }
}

@media (prefers-reduced-motion: reduce) {
  .is-live .live-dot {
    animation: none;
  }
}
</style>
