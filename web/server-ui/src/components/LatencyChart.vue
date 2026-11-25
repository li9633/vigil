<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { formatTime, toDate, type HeartbeatModule, type HeartbeatPoint } from '@vigil/shared'

const props = defineProps<{
  points: HeartbeatPoint[]
  modules: HeartbeatModule[]
}>()

const container = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let observer: ResizeObserver | null = null

const PALETTE = ['#2563eb', '#16a34a', '#d97706', '#7c3aed', '#0891b2']

function buildOption(): echarts.EChartsOption {
  const sorted = [...props.points]
    .filter((point) => toDate(point.ts))
    .sort((a, b) => (toDate(a.ts)?.getTime() ?? 0) - (toDate(b.ts)?.getTime() ?? 0))

  const categories = sorted.map((point) => formatTime(point.ts))
  const gaps = sorted.map((point, index) => {
    if (index === 0) return null
    const prev = toDate(sorted[index - 1].ts)
    const current = toDate(point.ts)
    if (!prev || !current) return null
    return Math.round((current.getTime() - prev.getTime()) / 1000)
  })

  const latencySeries = props.modules.slice(0, 4).map((module, index) => {
    const color = PALETTE[(index + 1) % PALETTE.length]
    return {
      name: module.name || module.id,
      type: 'line' as const,
      yAxisIndex: 1,
      smooth: true,
      showSymbol: false,
      connectNulls: true,
      lineStyle: { width: 1.5, color },
      itemStyle: { color },
      data: sorted.map((point) => point.modules?.[module.id]?.latency_ms ?? null),
    }
  })

  return {
    animation: false,
    grid: { left: 52, right: 56, top: 44, bottom: 30 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'line' } },
    legend: {
      top: 4,
      type: 'scroll',
      itemWidth: 14,
      itemHeight: 8,
      textStyle: { fontSize: 11 },
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: categories,
      axisLabel: { fontSize: 10, color: '#8a97a6' },
      axisLine: { lineStyle: { color: '#e4e7ed' } },
      axisTick: { show: false },
    },
    yAxis: [
      {
        type: 'value',
        name: '心跳间隔(s)',
        nameTextStyle: { fontSize: 10, color: '#8a97a6' },
        min: 0,
        axisLabel: { fontSize: 10, color: '#8a97a6' },
        splitLine: { lineStyle: { color: '#f0f2f5' } },
      },
      {
        type: 'value',
        name: '时延(ms)',
        nameTextStyle: { fontSize: 10, color: '#8a97a6' },
        min: 0,
        axisLabel: { fontSize: 10, color: '#8a97a6' },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: '心跳间隔(s)',
        type: 'line',
        yAxisIndex: 0,
        step: 'end',
        showSymbol: false,
        connectNulls: false,
        lineStyle: { width: 2, color: '#2563eb' },
        itemStyle: { color: '#2563eb' },
        areaStyle: { color: 'rgba(37, 99, 235, 0.07)' },
        data: gaps,
      },
      ...latencySeries,
    ],
  }
}

function render() {
  if (!chart) return
  chart.setOption(buildOption(), true)
}

onMounted(() => {
  if (!container.value) return
  chart = echarts.init(container.value)
  render()
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(container.value)
})

watch(
  () => [props.points, props.modules],
  () => render(),
  { deep: true },
)

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div ref="container" class="latency-chart"></div>
</template>

<style scoped lang="scss">
.latency-chart {
  width: 100%;
  height: 300px;
}

@media (max-width: 768px) {
  .latency-chart {
    height: 240px;
  }
}
</style>
