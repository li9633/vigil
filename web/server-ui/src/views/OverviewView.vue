<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  EmptyState,
  LiveBadge,
  LoadingBlock,
  RefreshFab,
  StatCard,
  StatusTag,
  TimeText,
  formatPercent,
  getErrorMessage,
  usePolling,
  type Overview,
} from '@vigil/shared'
import { adminApi } from '@/api/admin'
import { useServerStream } from '@/composables/useServerStream'

const overview = ref<Overview | null>(null)
const loading = ref(false)
const error = ref('')

interface StatItem {
  label: string
  value: string
  hint?: string
  tone?: 'ok' | 'warn' | 'critical'
  accent?: boolean
}

const statCards = computed<StatItem[]>(() => {
  const data = overview.value
  if (!data) return []
  const rate = data.notification_success_rate
  const rateTone: StatItem['tone'] | undefined =
    rate === null || rate === undefined
      ? undefined
      : (rate <= 1 ? rate * 100 : rate) >= 99
        ? 'ok'
        : (rate <= 1 ? rate * 100 : rate) >= 95
          ? 'warn'
          : 'critical'

  return [
    {
      label: '客户端总数',
      value: String(data.clients_total ?? 0),
      hint: `在线 ${data.clients_online ?? 0}`,
      accent: true,
    },
    {
      label: '当前离线',
      value: String(data.clients_down ?? 0),
      tone: (data.clients_down ?? 0) > 0 ? 'critical' : 'ok',
      hint: 'SUSPECT / DOWN',
      accent: true,
    },
    {
      label: '未闭环告警',
      value: String(data.open_incidents ?? 0),
      tone: (data.open_incidents ?? 0) > 0 ? 'critical' : 'ok',
      hint: '待处理 incident',
      accent: true,
    },
    {
      label: '近 24h 告警',
      value: String(data.incidents_24h ?? 0),
      hint: data.flapping_clients ? `抖动客户端 ${data.flapping_clients}` : '含已恢复',
    },
    {
      label: '通知成功率',
      value: formatPercent(data.notification_success_rate),
      tone: rateTone,
      hint: '近 24 小时',
      accent: true,
    },
  ]
})

const timeline = computed(() => {
  const items = overview.value?.timeline ?? []
  return [...items].sort((a, b) => {
    const left = new Date(a.ts).getTime()
    const right = new Date(b.ts).getTime()
    return (Number.isNaN(right) ? 0 : right) - (Number.isNaN(left) ? 0 : left)
  })
})

async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    overview.value = await adminApi.overview()
  } catch (err) {
    error.value = getErrorMessage(err, '总览数据加载失败')
  } finally {
    loading.value = false
  }
}

const poll = usePolling(load, {
  interval: 15000,
  isError: () => Boolean(error.value),
})
// SSE 只加速刷新，轮询保留作为降级通道
const stream = useServerStream({ onUpdate: () => poll.refresh() })
</script>

<template>
  <div class="page pb-fab">
    <div class="page-head">
      <div>
        <h2 class="page-title">运行总览</h2>
        <p class="page-sub">
          在线客户端、当前故障与近 24 小时告警
          <template v-if="overview?.generated_at">
            · 数据时间 <TimeText :value="overview.generated_at" mode="datetime" />
          </template>
        </p>
      </div>
      <LiveBadge :at="poll.lastUpdated" :paused="poll.paused" :streaming="stream.connected" />
    </div>

    <el-alert v-if="error" class="error-box" type="error" show-icon :closable="false">
      <div class="error-actions">
        <span>{{ error }}</span>
        <el-button size="small" :loading="poll.refreshing" @click="poll.retry">重试</el-button>
      </div>
    </el-alert>

    <LoadingBlock v-if="loading && !overview" min-height="220px" text="正在加载总览…" />

    <EmptyState
      v-else-if="!overview && !loading"
      title="暂未获取到总览数据"
      description="请确认服务端 API 可用后重试。"
    >
      <template #action>
        <el-button size="small" @click="poll.retry">重新加载</el-button>
      </template>
    </EmptyState>

    <template v-else-if="overview">
      <div class="stat-grid">
        <StatCard
          v-for="card in statCards"
          :key="card.label"
          :label="card.label"
          :value="card.value"
          :hint="card.hint"
          :tone="card.tone"
          :accent="card.accent"
          :pulse="card.tone === 'critical'"
        />
      </div>

      <div class="panel">
        <div class="panel-head">
          <span class="panel-title">近 24 小时告警时间线</span>
          <router-link class="panel-link" to="/incidents">查看告警中心</router-link>
        </div>
        <div class="panel-body panel-body--flush">
          <ul v-if="timeline.length" class="timeline">
            <li v-for="(item, index) in timeline" :key="item.id ?? `${item.ts}-${index}`" class="timeline-item">
              <div class="timeline-time">
                <TimeText :value="item.ts" mode="datetime" />
              </div>
              <StatusTag :status="item.severity || item.type" />
              <div class="timeline-main">
                <p class="timeline-summary">{{ item.summary || item.type }}</p>
                <p class="timeline-meta muted">
                  {{ item.client_name || item.client_id || '未知客户端' }} · {{ item.type }}
                </p>
              </div>
            </li>
          </ul>
          <EmptyState
            v-else
            compact
            title="近 24 小时没有告警"
            description="连接正常时这里保持为空。"
          />
        </div>
      </div>
    </template>

    <RefreshFab
      :loading="poll.refreshing"
      :paused="poll.paused"
      :error="Boolean(error)"
      @refresh="poll.refresh"
    />
  </div>
</template>

<style scoped lang="scss">
.panel-link {
  font-size: var(--vigil-font-xs);
}

.error-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--vigil-space-3);
}

.timeline {
  margin: 0;
  padding: 0;
  list-style: none;
}

.timeline-item {
  display: flex;
  align-items: flex-start;
  gap: var(--vigil-space-3);
  padding: var(--vigil-space-3) var(--vigil-space-4);
  border-bottom: 1px solid var(--vigil-color-border);

  &:last-child {
    border-bottom: none;
  }
}

.timeline-time {
  flex: 0 0 148px;
  font-size: var(--vigil-font-xs);
  color: var(--vigil-color-text-muted);
}

.timeline-main {
  flex: 1;
  min-width: 0;
}

.timeline-summary {
  margin: 0;
  font-size: var(--vigil-font-sm);
  color: var(--vigil-color-text);
  overflow-wrap: anywhere;
}

.timeline-meta {
  margin: 2px 0 0;
  font-size: var(--vigil-font-xs);
}

@media (max-width: 640px) {
  .timeline-item {
    flex-wrap: wrap;
  }

  .timeline-time {
    flex-basis: 100%;
  }
}
</style>
