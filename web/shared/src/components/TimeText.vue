<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { formatDateTime, formatRelative, formatTime, toDate, type TimeInput } from '../format'

const props = withDefaults(
  defineProps<{
    value: TimeInput
    mode?: 'relative' | 'datetime' | 'time'
    fallback?: string
  }>(),
  { mode: 'relative', fallback: '—' },
)

const now = ref(Date.now())
let timer: number | undefined

const date = computed(() => toDate(props.value))
const iso = computed(() => (date.value ? date.value.toISOString() : undefined))
const absolute = computed(() => formatDateTime(props.value, props.fallback))
const text = computed(() => {
  if (!date.value) return props.fallback
  if (props.mode === 'datetime') return absolute.value
  if (props.mode === 'time') return formatTime(props.value, props.fallback)
  return formatRelative(props.value, now.value, props.fallback)
})

onMounted(() => {
  timer = window.setInterval(() => {
    now.value = Date.now()
  }, 30000)
})

onBeforeUnmount(() => {
  if (timer !== undefined) window.clearInterval(timer)
})
</script>

<template>
  <time class="time-text" :datetime="iso" :title="absolute">{{ text }}</time>
</template>

<style scoped lang="scss">
.time-text {
  color: inherit;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
</style>
