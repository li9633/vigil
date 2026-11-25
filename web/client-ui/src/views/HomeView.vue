<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  EmptyState,
  LiveBadge,
  LoadingBlock,
  RefreshFab,
  StatusTag,
  TimeText,
  booleanStatus,
  formatDuration,
  formatLatency,
  getErrorMessage,
  usePolling,
  type ClientStatusSnapshot,
} from '@vigil/shared'
import { clientApi } from '@/api/client'

const status = ref<ClientStatusSnapshot | null>(null)
const loading = ref(false)
const error = ref('')

const modules = computed(() => status.value?.modules ?? [])
const guards = computed(() => status.value?.guards ?? [])
const latestChecks = computed(() => (status.value?.latest_checks ?? []).slice(0, 5))
const serverConnected = computed(() => status.value?.server_connected ?? null)
const spool = computed(() => status.value?.spool ?? null)

const infoCells = computed(() => {
  const data = status.value
  if (!data) return []
  return [
    { label: 'WAN IP', value: data.wan_ip || '—' },
    { label: 'LAN IP', value: data.lan_ip || '—' },
    { label: '接入方式', value: data.carrier || '—' },
    { label: 'PPPoE 时长', value: formatDuration(data.pppoe_uptime) },
    { label: 'Agent 版本', value: data.agent_version || '—' },
    {
      label: '待上传事件',
      value: data.spool ? `${data.spool.pending} 条（丢弃 ${data.spool.dropped}）` : '—',
    },
    { label: 'boot_id', value: data.boot_id || '—' },
    {
      label: '维护窗口',
      value: data.maintenance ? '生效中（禁止动作）' : '未启用',
    },
  ]
})

async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    status.value = await clientApi.status()
  } catch (err) {
    error.value = getErrorMessage(err, '状态加载失败')
  } finally {
    loading.value = false
  }
}

const poll = usePolling(load, {
  interval: 5000,
  isError: () => Boolean(error.value),
})
</script>

<template>
  <div class="page pb-fab">
    <div class="page-head">
      <div>
        <h2 class="page-title">连接状态</h2>
        <p class="page-sub">每 5 秒自动更新，写操作请在“动作”页执行</p>
      </div>
      <LiveBadge :at="poll.lastUpdated" :paused="poll.paused" />
    </div>

    <el-alert v-if="error" class="error-box" type="error" show-icon :closable="false">
      <div class="alert-row">
        <span>{{ error }}</span>
        <el-button size="small" text type="primary" @click="poll.retry">重试</el-button>
      </div>
    </el-alert>

    <LoadingBlock v-if="loading && !status" min-height="200px" text="正在读取设备状态…" />

    <EmptyState
      v-else-if="!status && !loading"
      title="暂未获取到状态"
      description="请确认 agent 服务已启动后重试。"
    >
      <template #action>
        <el-button size="small" @click="poll.retry">重新加载</el-button>
      </template>
    </EmptyState>

    <template v-else-if="status">
      <div class="panel">
        <div class="panel-body state-panel">
          <div class="state-main">
            <span class="state-caption">当前状态</span>
            <StatusTag :status="status.state" />
            <span class="state-hint muted">
              更新于 <TimeText :value="status.generated_at || poll.lastUpdated" /> · 运行
              {{ formatDuration(status.uptime) }}
            </span>
          </div>
          <div class="state-server">
            <span class="state-caption">服务端连接</span>
            <StatusTag
              :status="booleanStatus(serverConnected)"
              :label="serverConnected === null ? '未知' : serverConnected ? '已连接' : '未连接'"
            />
          </div>
        </div>
      </div>

      <div class="info-grid">
        <div v-for="cell in infoCells" :key="cell.label" class="info-cell">
          <span class="info-label">{{ cell.label }}</span>
          <span class="info-value mono">{{ cell.value }}</span>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <span class="panel-title">守护项</span>
          <router-link class="panel-link" to="/guards">全部</router-link>
        </div>
        <ul v-if="guards.length" class="list">
          <li v-for="guard in guards" :key="guard.id" class="list-item">
            <div class="list-main">
              <p class="list-title">{{ guard.name || guard.id }}</p>
              <p class="list-meta">
                {{ guard.template || '—' }} · 检测 {{ guard.check_status || '—' }}
              </p>
            </div>
            <div class="list-side">
              <StatusTag :status="guard.status" size="sm" />
            </div>
          </li>
        </ul>
        <div v-else class="panel-body">
          <EmptyState compact title="暂无守护项" description="尚未配置守护项，或等待首轮检测结果。" />
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <span class="panel-title">模块状态</span>
          <router-link class="panel-link" to="/checks">查看检测项</router-link>
        </div>
        <ul v-if="modules.length" class="list">
          <li v-for="module in modules" :key="module.id" class="list-item">
            <div class="list-main">
              <p class="list-title mono">{{ module.name || module.id }}</p>
              <p class="list-meta">{{ module.layer || module.id }}</p>
            </div>
            <div class="list-side">
              <StatusTag :status="module.status" size="sm" />
              <span>{{ formatLatency(module.latency_ms) }}</span>
            </div>
          </li>
        </ul>
        <div v-else class="panel-body">
          <EmptyState compact title="暂无模块数据" description="等待下一次心跳上报模块快照。" />
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <span class="panel-title">最新检测结果</span>
          <router-link class="panel-link" to="/checks">全部</router-link>
        </div>
        <ul v-if="latestChecks.length" class="list">
          <li v-for="check in latestChecks" :key="check.id" class="list-item">
            <div class="list-main">
              <p class="list-title">{{ check.name || check.id }}</p>
              <p class="list-meta">{{ check.evidence || check.layer || '—' }}</p>
            </div>
            <div class="list-side">
              <StatusTag :status="check.status" size="sm" />
              <span>{{ formatLatency(check.latency_ms) }}</span>
            </div>
          </li>
        </ul>
        <div v-else class="panel-body">
          <EmptyState compact title="暂无检测结果" description="检测模块运行后会在此展示。" />
        </div>
      </div>

      <p v-if="spool && spool.pending > 0" class="spool-hint muted">
        本地上报队列中还有 {{ spool.pending }} 条事件等待发送。
      </p>
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
.alert-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--vigil-space-2);
}

.state-panel {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--vigil-space-3);
  flex-wrap: wrap;
}

.state-main {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
}

.state-caption {
  font-size: 11px;
  color: var(--vigil-color-text-muted);
}

.state-hint {
  font-size: 11px;
}

.state-server {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
}

.panel-link {
  font-size: var(--vigil-font-xs);
}

.spool-hint {
  margin: 0;
  font-size: 11px;
  text-align: center;
}
</style>
