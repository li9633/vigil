<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  EmptyState,
  GuardEditor,
  LiveBadge,
  LoadingBlock,
  RefreshFab,
  StatusTag,
  TimeText,
  formatDuration,
  formatLatency,
  getErrorMessage,
  usePolling,
  type ClientDetail,
  type GuardDefinition,
  type Incident,
} from '@vigil/shared'
import { adminApi } from '@/api/admin'
import LatencyChart from '@/components/LatencyChart.vue'

const route = useRoute()
const clientId = computed(() => String(route.params.id ?? ''))

const detail = ref<ClientDetail | null>(null)
const loading = ref(false)
const error = ref('')

const guardConfig = ref<GuardDefinition[]>([])
const guardManaged = ref(false)
const guardError = ref('')
const guardSaving = ref(false)
const guardEditorOpen = ref(false)
const guardEditorSelected = ref('')

const client = computed(() => detail.value?.client ?? null)
const modules = computed(() => detail.value?.modules ?? [])
const guards = computed(() => detail.value?.guards ?? [])
const heartbeats = computed(() => detail.value?.heartbeats ?? [])
function recoveryIdOf(incident: Incident): string {
  const value = incident.evidence?.recovery_id
  return typeof value === 'string' ? value : ''
}

const incidents = computed(() =>
  [...(detail.value?.incidents ?? [])].sort(
    (a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime(),
  ),
)
const events = computed(() =>
  [...(detail.value?.recent_events ?? [])].sort(
    (a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime(),
  ),
)
const actions = computed(() => detail.value?.actions ?? [])

const hasChartData = computed(() => {
  const points = heartbeats.value
  if (points.length < 2) return false
  return points.some((point) => {
    if (typeof point.latency_ms === 'number') return true
    const moduleValues = Object.values(point.modules ?? {})
    return moduleValues.some((module) => typeof module?.latency_ms === 'number')
  })
})

async function load() {
  if (loading.value) return
  if (!clientId.value) {
    error.value = '缺少客户端 ID'
    return
  }
  loading.value = true
  error.value = ''
  guardError.value = ''
  try {
    const [detailData, guardData] = await Promise.all([
      adminApi.client(clientId.value),
      adminApi.clientGuards(clientId.value).catch((err) => {
        guardError.value = getErrorMessage(err, '守护项配置加载失败')
        return null
      }),
    ])
    detail.value = detailData
    if (guardData) {
      guardConfig.value = Array.isArray(guardData.items) ? guardData.items : []
      guardManaged.value = Boolean(guardData.managed)
    }
  } catch (err) {
    error.value = getErrorMessage(err, '客户端详情加载失败')
  } finally {
    loading.value = false
  }
}

function openGuardEditor(selectedId = ''): void {
  guardEditorSelected.value = selectedId
  guardEditorOpen.value = true
}

async function handleGuardSave(payload: GuardDefinition[]) {
  if (guardSaving.value || !clientId.value) return
  guardSaving.value = true
  try {
    const result = await adminApi.saveClientGuards(clientId.value, payload)
    guardConfig.value = result.items
    guardManaged.value = result.managed
    guardEditorOpen.value = false
    ElMessage.success('守护项配置已下发，客户端将在下次配置轮询时生效')
  } catch (err) {
    ElMessage.error(getErrorMessage(err, '下发失败，请检查配置'))
  } finally {
    guardSaving.value = false
  }
}

async function clearGuardConfig() {
  if (!clientId.value) return
  try {
    await ElMessageBox.confirm(
      '清除后客户端将回退到本地守护项配置，确定继续？',
      '清除服务端下发',
      { type: 'warning', confirmButtonText: '清除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const result = await adminApi.clearClientGuards(clientId.value)
    guardConfig.value = []
    guardManaged.value = result.managed
    ElMessage.success('已清除服务端下发配置')
  } catch (err) {
    ElMessage.error(getErrorMessage(err, '清除失败'))
  }
}

watch(clientId, load)

const poll = usePolling(load, {
  interval: 15000,
  isError: () => Boolean(error.value),
})
</script>

<template>
  <div class="page pb-fab">
    <div class="page-head">
      <div>
        <router-link class="back-link" to="/clients">← 返回客户端列表</router-link>
        <h2 class="page-title detail-title">
          {{ client?.name || '客户端详情' }}
          <StatusTag v-if="client" :status="client.state" />
        </h2>
        <p class="page-sub mono">{{ clientId }}</p>
      </div>
      <LiveBadge :at="poll.lastUpdated" :paused="poll.paused" />
    </div>

    <el-alert
      v-if="error"
      class="error-box"
      type="error"
      show-icon
      :closable="false"
      :title="error"
    >
      <el-button size="small" text type="primary" @click="poll.retry">重试</el-button>
    </el-alert>

    <LoadingBlock v-if="loading && !detail" min-height="240px" text="正在加载客户端详情…" />

    <EmptyState
      v-else-if="!detail && !loading"
      title="未找到客户端数据"
      description="客户端可能已被删除，或接口暂无返回。"
    >
      <template #action>
        <el-button size="small" @click="poll.retry">重新加载</el-button>
      </template>
    </EmptyState>

    <template v-else-if="detail && client">
      <div class="panel">
        <div class="panel-body">
          <dl class="meta-grid">
            <div class="meta-item">
              <dt>状态</dt>
              <dd><StatusTag :status="client.state" /></dd>
            </div>
            <div class="meta-item">
              <dt>WAN IP</dt>
              <dd class="mono">{{ client.wan_ip || '—' }}</dd>
            </div>
            <div class="meta-item">
              <dt>最后心跳</dt>
              <dd><TimeText :value="client.last_seen" /></dd>
            </div>
            <div class="meta-item">
              <dt>最后 beacon</dt>
              <dd><TimeText :value="client.last_beacon" /></dd>
            </div>
            <div class="meta-item">
              <dt>Agent 版本</dt>
              <dd class="mono">{{ client.agent_version || '—' }}</dd>
            </div>
            <div class="meta-item">
              <dt>固件 / 型号</dt>
              <dd class="mono">{{ [client.fw_version, client.model].filter(Boolean).join(' / ') || '—' }}</dd>
            </div>
            <div class="meta-item wide">
              <dt>boot_id</dt>
              <dd class="mono">{{ client.boot_id || '—' }}</dd>
            </div>
          </dl>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <span class="panel-title">守护项</span>
          <span class="muted">{{ guards.length }} 项</span>
        </div>
        <div class="panel-body panel-body--flush">
          <el-table :data="guards" size="small">
            <el-table-column label="名称" min-width="160">
              <template #default="{ row }">
                <span>{{ row.name || row.id }}</span>
              </template>
            </el-table-column>
            <el-table-column label="模板" width="130">
              <template #default="{ row }">
                <span class="mono muted">{{ row.template || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="110">
              <template #default="{ row }">
                <StatusTag :status="row.status" size="sm" />
              </template>
            </el-table-column>
            <el-table-column label="检测" width="100">
              <template #default="{ row }">
                <StatusTag :status="row.check_status || 'UNKNOWN'" size="sm" />
              </template>
            </el-table-column>
            <el-table-column label="连续失败" width="100">
              <template #default="{ row }">
                <span class="mono">{{ row.failure_count ?? 0 }}</span>
              </template>
            </el-table-column>
            <el-table-column label="Check" min-width="180">
              <template #default="{ row }">
                <span class="mono muted">{{ Object.keys(row.checks ?? {}).length }} 项</span>
              </template>
            </el-table-column>
            <template #empty>
              <EmptyState compact title="暂无守护项" description="等待客户端心跳上报守护项快照。" />
            </template>
          </el-table>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <span class="panel-title">守护项配置（下发）</span>
          <div class="panel-actions">
            <el-tag v-if="guardManaged" size="small" type="success">已下发</el-tag>
            <el-tag v-else size="small" type="info">未下发</el-tag>
            <el-button size="small" type="primary" @click="openGuardEditor('')">
              编辑配置
            </el-button>
            <el-button
              v-if="guardManaged"
              size="small"
              text
              type="danger"
              @click="clearGuardConfig"
            >
              清除下发
            </el-button>
          </div>
        </div>
        <el-alert
          v-if="guardError"
          class="guard-error"
          type="error"
          show-icon
          :closable="false"
          :title="guardError"
        >
          <el-button size="small" text type="primary" @click="load">重试</el-button>
        </el-alert>
        <div class="panel-body panel-body--flush">
          <el-table :data="guardConfig" size="small">
            <el-table-column label="名称" min-width="160">
              <template #default="{ row }">
                <span>{{ row.name || row.id }}</span>
              </template>
            </el-table-column>
            <el-table-column label="模板" width="120">
              <template #default="{ row }">
                <span class="mono muted">{{ row.template || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="启用" width="80">
              <template #default="{ row }">
                <StatusTag
                  :status="row.enabled === false ? 'DISABLED' : 'OK'"
                  :label="row.enabled === false ? '停用' : '启用'"
                  size="sm"
                />
              </template>
            </el-table-column>
            <el-table-column label="检测项" width="90">
              <template #default="{ row }">
                <span class="mono">{{ (row.checks ?? []).length }}</span>
              </template>
            </el-table-column>
            <el-table-column label="触发阈值" width="130">
              <template #default="{ row }">
                <span class="mono">
                  {{ row.policy?.fail_threshold ?? 2 }} 次 / {{ row.policy?.interval ?? 30 }}s
                </span>
              </template>
            </el-table-column>
            <el-table-column label="恢复动作" width="100">
              <template #default="{ row }">
                <span class="mono">{{ (row.recovery ?? []).length }}</span>
              </template>
            </el-table-column>
            <template #empty>
              <EmptyState
                compact
                title="未下发守护项配置"
                description="客户端将使用本地 config.json 中的守护项；点击“编辑配置”可创建并下发。"
              />
            </template>
          </el-table>
        </div>
        <div class="panel-body">
          <p class="muted guard-hint">
            保存后客户端在下一次配置轮询（默认 ≤ 5 分钟）自动生效，届时客户端本地 UI 显示为只读。
          </p>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <span class="panel-title">模块状态</span>
          <span class="muted">{{ modules.length }} 个模块</span>
        </div>
        <div class="panel-body panel-body--flush">
          <el-table :data="modules" size="small">
            <el-table-column prop="id" label="模块" min-width="130">
              <template #default="{ row }">
                <span class="mono">{{ row.name || row.id }}</span>
              </template>
            </el-table-column>
            <el-table-column label="层级" width="90">
              <template #default="{ row }">
                <span class="mono muted">{{ row.layer || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="110">
              <template #default="{ row }">
                <StatusTag :status="row.status" size="sm" />
              </template>
            </el-table-column>
            <el-table-column label="时延" width="90">
              <template #default="{ row }">
                <span class="mono">{{ formatLatency(row.latency_ms) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="uptime" width="110">
              <template #default="{ row }">
                <span class="mono">{{ formatDuration(row.uptime) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="证据 / 备注" min-width="200">
              <template #default="{ row }">
                <span class="evidence">{{ row.message || row.evidence || '—' }}</span>
              </template>
            </el-table-column>
            <template #empty>
              <EmptyState compact title="暂无模块数据" description="等待下一次心跳上报模块快照。" />
            </template>
          </el-table>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <span class="panel-title">心跳与模块时延</span>
          <span class="muted">{{ heartbeats.length }} 个采样点</span>
        </div>
        <div class="panel-body">
          <LatencyChart v-if="hasChartData" :points="heartbeats" :modules="modules" />
          <EmptyState
            v-else
            compact
            title="数据不足，无法绘制趋势"
            description="图表需要至少 2 个包含时延数据的心跳采样点；当前采集窗口内数据不足。"
          />
        </div>
      </div>

      <div class="split-grid">
        <div class="panel">
          <div class="panel-head">
            <span class="panel-title">最近告警</span>
            <router-link class="panel-link" to="/incidents">告警中心</router-link>
          </div>
          <div class="panel-body panel-body--flush">
            <ul v-if="incidents.length" class="compact-list">
              <li v-for="incident in incidents.slice(0, 6)" :key="incident.id" class="compact-item">
                <div class="compact-main">
                  <StatusTag :status="incident.state" size="sm" />
                  <span class="compact-title">
                    <template v-if="incident.guard_name">{{ incident.guard_name }} · </template>
                    {{ incident.summary || incident.category }}
                  </span>
                </div>
                <span class="compact-meta muted">
                  <span v-if="recoveryIdOf(incident)" class="mono" :title="recoveryIdOf(incident)">
                    recovery {{ recoveryIdOf(incident).slice(0, 8) }}…
                  </span>
                  <TimeText :value="incident.started_at" />
                </span>
              </li>
            </ul>
            <EmptyState v-else compact title="暂无告警记录" />
          </div>
        </div>

        <div class="panel">
          <div class="panel-head">
            <span class="panel-title">最近事件</span>
            <span class="muted">{{ events.length }} 条</span>
          </div>
          <div class="panel-body panel-body--flush">
            <ul v-if="events.length" class="compact-list">
              <li v-for="event in events.slice(0, 6)" :key="event.event_id" class="compact-item">
                <div class="compact-main">
                  <StatusTag :status="event.level || event.type" size="sm" />
                  <span class="compact-title">{{ event.message || event.type }}</span>
                </div>
                <span class="compact-meta muted">
                  <TimeText :value="event.ts" />
                </span>
              </li>
            </ul>
            <EmptyState v-else compact title="暂无事件" description="agent 尚未上报事件。" />
          </div>
        </div>
      </div>

      <div v-if="actions.length" class="panel">
        <div class="panel-head">
          <span class="panel-title">动作记录</span>
          <span class="muted">{{ actions.length }} 条</span>
        </div>
        <div class="panel-body panel-body--flush">
          <el-table :data="actions" size="small">
            <el-table-column prop="action" label="动作" min-width="150" />
            <el-table-column prop="module" label="模块" width="120" />
            <el-table-column prop="risk" label="级别" width="70" />
            <el-table-column label="开始时间" width="150">
              <template #default="{ row }">
                <TimeText :value="row.started_at" mode="datetime" />
              </template>
            </el-table-column>
            <el-table-column label="结果" width="100">
              <template #default="{ row }">
                <StatusTag :status="row.result" size="sm" />
              </template>
            </el-table-column>
            <el-table-column prop="exit_code" label="退出码" width="80" />
          </el-table>
        </div>
      </div>
    </template>

    <GuardEditor
      v-model="guardEditorOpen"
      :guards="guardConfig"
      :busy="guardSaving"
      :selected-id="guardEditorSelected"
      title="配置下发守护项"
      hint="保存后写入服务端并通过 /api/v1/config 下发；客户端本地配置将变为只读。"
      @save="handleGuardSave"
    />

    <RefreshFab
      :loading="poll.refreshing"
      :paused="poll.paused"
      :error="Boolean(error)"
      @refresh="poll.refresh"
    />
  </div>
</template>

<style scoped lang="scss">
.back-link {
  display: inline-block;
  margin-bottom: var(--vigil-space-1);
  font-size: var(--vigil-font-xs);
}

.detail-title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--vigil-space-2);
}

.meta-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: var(--vigil-space-3) var(--vigil-space-4);
  margin: 0;
}

.meta-item {
  min-width: 0;

  dt {
    font-size: var(--vigil-font-xs);
    color: var(--vigil-color-text-muted);
    margin-bottom: 2px;
  }

  dd {
    margin: 0;
    font-size: var(--vigil-font-sm);
    overflow-wrap: anywhere;
  }

  &.wide {
    grid-column: 1 / -1;
  }
}

.evidence {
  display: block;
  font-size: var(--vigil-font-xs);
  color: var(--vigil-color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.split-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: var(--vigil-space-4);
}

.compact-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.compact-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--vigil-space-3);
  padding: 10px var(--vigil-space-4);
  border-bottom: 1px solid var(--vigil-color-border);

  &:last-child {
    border-bottom: none;
  }
}

.compact-main {
  display: flex;
  align-items: center;
  gap: var(--vigil-space-2);
  min-width: 0;
}

.compact-title {
  font-size: var(--vigil-font-xs);
  color: var(--vigil-color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.compact-meta {
  flex: 0 0 auto;
  font-size: var(--vigil-font-xs);
}

.panel-link {
  font-size: var(--vigil-font-xs);
}

.panel-actions {
  display: flex;
  align-items: center;
  gap: var(--vigil-space-2);
}

.guard-error {
  margin: var(--vigil-space-3) var(--vigil-space-3) 0;
}

.guard-hint {
  margin: 0;
  font-size: var(--vigil-font-xs);
}
</style>
