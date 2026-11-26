<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElTooltip } from 'element-plus'
import {
  formatDateTime,
  formatRelative,
  formatRelativeFuture,
  formatTime,
  toDate,
  toIso,
  type TimeInput,
} from '../utils/time'

type TooltipPlacement = 'top' | 'bottom' | 'left' | 'right' | 'top-start' | 'top-end'

const props = withDefaults(
  defineProps<{
    value: TimeInput
    mode?: 'relative' | 'datetime' | 'time'
    maxAgeYears?: number
    fallback?: string
    placement?: TooltipPlacement
    showTooltip?: boolean
  }>(),
  {
    mode: 'relative',
    maxAgeYears: 3,
    fallback: '—',
    placement: 'top',
    showTooltip: true,
  },
)

const nowMs = ref(Date.now())
let timer: number | undefined

const date = computed(() => toDate(props.value))
const iso = computed(() => toIso(props.value))
const absolute = computed(() => formatDateTime(props.value, props.fallback))
const text = computed(() => {
  if (!date.value) return props.fallback
  if (props.mode === 'datetime') return absolute.value
  if (props.mode === 'time') return formatTime(props.value, props.fallback)
  return date.value.getTime() > nowMs.value
    ? formatRelativeFuture(props.value, nowMs.value, props.fallback, props.maxAgeYears)
    : formatRelative(props.value, nowMs.value, props.fallback, props.maxAgeYears)
})

onMounted(() => {
  timer = window.setInterval(() => {
    nowMs.value = Date.now()
  }, 30000)
})

onBeforeUnmount(() => {
  if (timer !== undefined) window.clearInterval(timer)
})
</script>

<template>
  <el-tooltip
    :content="absolute"
    :placement="placement"
    :disabled="!date || !showTooltip"
    :show-after="200"
  >
    <time class="time-text" :datetime="iso" :aria-label="absolute" tabindex="0">{{ text }}</time>
  </el-tooltip>
</template>
