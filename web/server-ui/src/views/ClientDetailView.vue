<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  EmptyState,
  LiveBadge,
  LoadingBlock,
  RefreshFab,
  StatusTag,
  TimeText,
  clientHealthLabel,
  copyText,
  formatLatency,
  getErrorMessage,
  usePolling,
  type ClientDetail,
  type Incident,
} from '@vigil/shared'
import { adminApi } from '@/api/admin'
import LatencyChart from '@/components/LatencyChart.vue'

const route = useRoute()
const router = useRouter()
const clientId = computed(() => String(route.params.id ?? ''))

const detail = ref<ClientDetail | null>(null)
const loading = ref(false)
const error = ref('')

const client = computed(() => detail.value?.client ?? null)
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

interface CheckRow {
  id: string
  name: string
  probe: string
  status: string
  latency_ms: number | null
  evidence: Record<string, unknown> | null
}

const checkRows = computed<CheckRow[]>(() =>
  (detail.value?.checks ?? []).map((item) => ({
    id: item.check_id,
    name: item.name || item.check_id,
    probe: item.probe || '',
    status: item.status || 'unknown',
    latency_ms: item.latency_ms ?? null,
    evidence: item.evidence ?? null,
  })),
)

const hasChartData = computed(() => {
  const points = heartbeats.value
  if (points.length < 2) return false
  return points.some((point) => {
    if (typeof point.latency_ms === 'number') return true
    const values = Object.values(point.checks ?? {})
    return values.some((item) => typeof item?.latency_ms === 'number')
  })
})

function evidenceText(evidence: Record<string, unknown> | null | undefined): string {
  if (!evidence || !Object.keys(evidence).length) return '—'
  const text = JSON.stringify(evidence)
  return text.length > 100 ? `${text.slice(0, 100)}…` : text
}

async function load() {
  if (loading.value) return
  if (!clientId.value) {
    error.value = '缺少客户端 ID'
    return
  }
  loading.value = true
  error.value = ''
  try {
    detail.value = await adminApi.client(clientId.value)
  } catch (err) {
    error.value = getErrorMessage(err, '客户端详情加载失败')
  } finally {
    loading.value = false
  }
}

watch(clientId, load)

const poll = usePolling(load, {
  interval: 15000,
  isError: () => Boolean(error.value),
})

const rotating = ref(false)
const secretDialogOpen = ref(false)
const rotatedSecret = ref('')

async function rotateSecret() {
  if (!clientId.value || rotating.value) return
  try {
    await ElMessageBox.confirm(
      '轮换后旧密钥立即失效，客户端会认证失败，直到在设备上更新为新密钥。新密钥只显示一次，确定继续？',
      '轮换客户端密钥',
      { type: 'warning', confirmButtonText: '轮换', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  rotating.value = true
  try {
    const result = await adminApi.rotateClientSecret(clientId.value)
    rotatedSecret.value = result.secret
    secretDialogOpen.value = true
    ElMessage.success('客户端密钥已轮换')
  } catch (err) {
    ElMessage.error(getErrorMessage(err, '轮换失败'))
  } finally {
    rotating.value = false
  }
}

async function copyRotatedSecret() {
  const ok = await copyText(rotatedSecret.value)
  if (ok) ElMessage.success('已复制到剪贴板')
  else ElMessage.warning('复制失败，请手动选中复制')
}

const deleting = ref(false)

async function deleteClient() {
  if (!clientId.value || deleting.value) return
  try {
    await ElMessageBox.confirm(
      `将彻底删除设备「${client.value?.name || clientId.value}」及其心跳、事件、告警、通知等全部历史数据，且不可恢复。确定继续？`,
      '删除客户端',
      { type: 'warning', confirmButtonText: '彻底删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  deleting.value = true
  try {
    await adminApi.deleteClient(clientId.value)
    ElMessage.success('客户端已删除')
    router.push({ name: 'clients' })
  } catch (err) {
    ElMessage.error(getErrorMessage(err, '删除失败'))
  } finally {
    deleting.value = false
  }
}
</script>

<template>
  <div class="page pb-fab">
    <div class="page-head">
      <div>
        <router-link class="back-link" to="/clients">← 返回客户端列表</router-link>
        <h2 class="page-title detail-title">
          {{ client?.name || '客户端详情' }}
          <template v-if="client">
            <StatusTag :status="client.lifecycle" />
            <StatusTag :status="client.state" :label="clientHealthLabel(client.state)" size="sm" />
          </template>
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
              <dt>连接状态</dt>
              <dd><StatusTag :status="client.lifecycle" /></dd>
            </div>
            <div class="meta-item">
              <dt>健康状态</dt>
              <dd><StatusTag :status="client.state" :label="clientHealthLabel(client.state)" /></dd>
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
              <dt>最后保活信号</dt>
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
            <div v-if="client.revoked_at" class="meta-item wide">
              <dt>失效时间</dt>
              <dd>
                <TimeText :value="client.revoked_at" mode="datetime" />
                <span v-if="client.revoked_reason" class="muted"> · {{ client.revoked_reason }}</span>
              </dd>
            </div>
          </dl>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <span class="panel-title">访问凭证</span>
          <el-button size="small" :loading="rotating" @click="rotateSecret">
            轮换密钥
          </el-button>
        </div>
        <div class="panel-body">
          <p class="muted secret-hint">
            客户端密钥只保存加密摘要，服务端无法查看明文。轮换后需同步修改设备配置，否则该客户端会认证失败。
          </p>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <span class="panel-title">检测项</span>
          <div class="panel-actions">
            <el-tag size="small" type="info">客户端上报</el-tag>
          </div>
        </div>
        <div class="panel-body panel-body--flush">
          <el-table :data="checkRows" size="small">
            <el-table-column label="名称" min-width="150">
              <template #default="{ row }">
                <span>{{ row.name || row.id }}</span>
                <el-tag v-if="row.status === 'skipped'" size="small" type="info">跳过</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="ID" width="150">
              <template #default="{ row }">
                <span class="mono">{{ row.id }}</span>
              </template>
            </el-table-column>
            <el-table-column label="探测器" width="110">
              <template #default="{ row }">
                <span class="mono muted">{{ row.probe }}</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <StatusTag :status="row.status" size="sm" />
              </template>
            </el-table-column>
            <el-table-column label="时延" width="90">
              <template #default="{ row }">
                <span class="mono">{{ formatLatency(row.latency_ms) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="证据" min-width="200">
              <template #default="{ row }">
                <span class="evidence">{{ evidenceText(row.evidence) }}</span>
              </template>
            </el-table-column>
            <template #empty>
              <EmptyState
                compact
                title="暂无检测项数据"
                description="等待客户端心跳上报检测项结果；检测项由客户端本地配置与管理。"
              />
            </template>
          </el-table>
        </div>
        <div class="panel-body">
          <p class="muted check-hint">
            检测项由客户端本地配置与管理，服务端仅展示最近一次心跳上报的状态与证据。
          </p>
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
        <div class="panel-body">
          <p class="muted guard-hint">
            守护项由客户端本地配置与管理，服务端仅展示最近一次心跳上报的快照。
          </p>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <span class="panel-title">心跳与检测时延</span>
          <span class="muted">{{ heartbeats.length }} 个采样点</span>
        </div>
        <div class="panel-body">
          <LatencyChart v-if="hasChartData" :points="heartbeats" :checks="checkRows" />
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
            <EmptyState v-else compact title="暂无事件" description="客户端尚未上报事件。" />
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

      <div class="panel">
        <div class="panel-head">
          <div>
            <span class="panel-title">删除客户端</span>
            <p class="panel-sub">
              客户端取消注册后仅标记为失效；如需彻底清除，请在此删除设备及全部关联数据。
            </p>
          </div>
          <el-button type="danger" plain size="small" :loading="deleting" @click="deleteClient">
            删除客户端
          </el-button>
        </div>
      </div>
    </template>

    <el-dialog
      v-model="secretDialogOpen"
      title="新的客户端密钥"
      width="520px"
      :close-on-click-modal="false"
      @closed="rotatedSecret = ''"
    >
      <el-alert
        type="warning"
        show-icon
        :closable="false"
        title="仅显示一次，请立即复制并更新到设备配置"
      />
      <div class="secret-row">
        <el-input :model-value="rotatedSecret" readonly class="mono" />
        <el-button size="small" type="primary" @click="copyRotatedSecret">复制</el-button>
      </div>
      <template #footer>
        <el-button size="small" @click="secretDialogOpen = false">我已保存</el-button>
      </template>
    </el-dialog>

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

.guard-error,
.check-error {
  margin: var(--vigil-space-3) var(--vigil-space-3) 0;
}

.guard-hint,
.check-hint {
  margin: 0;
  font-size: var(--vigil-font-xs);
}

.secret-hint {
  margin: 0;
  font-size: var(--vigil-font-xs);
}

.secret-row {
  display: flex;
  align-items: center;
  gap: var(--vigil-space-2);
  margin-top: var(--vigil-space-3);
}
</style>
