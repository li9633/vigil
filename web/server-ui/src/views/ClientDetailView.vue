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
  copyText,
  formatLatency,
  getErrorMessage,
  usePolling,
  type CheckDefinition,
  type ClientDetail,
  type GuardDefinition,
  type Incident,
  type ProbeCatalogItem,
} from '@vigil/shared'
import { adminApi } from '@/api/admin'
import CheckEditor from '@/components/CheckEditor.vue'
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

const checkConfig = ref<CheckDefinition[]>([])
const checkManaged = ref(false)
const checkError = ref('')
const checkSaving = ref(false)
const checkEditorOpen = ref(false)
const probes = ref<ProbeCatalogItem[]>([])
const probeUpdatedAt = ref<string | null>(null)

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

interface CheckRow extends CheckDefinition {
  status: string
  latency_ms: number | null
  evidence: Record<string, unknown> | null
  probe_missing: boolean
  local?: boolean
}

const latestChecks = computed(() => {
  const map = new Map<string, NonNullable<ClientDetail['checks']>[number]>()
  for (const item of detail.value?.checks ?? []) map.set(item.check_id, item)
  return map
})

const probeIds = computed(() => new Set(probes.value.map((probe) => probe.id)))

const checkRows = computed<CheckRow[]>(() => {
  if (checkConfig.value.length) {
    return checkConfig.value.map((check) => {
      const latest = latestChecks.value.get(check.id)
      return {
        ...check,
        status: check.enabled === false ? 'skipped' : latest?.status || 'unknown',
        latency_ms: latest?.latency_ms ?? null,
        evidence: latest?.evidence ?? null,
        probe_missing: probes.value.length > 0 && !probeIds.value.has(check.probe),
      }
    })
  }
  // 未下发时展示客户端本地检测项的最近结果，便于以现状创建服务端配置
  return (detail.value?.checks ?? []).map((item) => ({
    id: item.check_id,
    name: item.name || item.check_id,
    probe: item.probe || '',
    params: {},
    expect: {},
    timeout: null,
    depends_on: [],
    enabled: true,
    status: item.status || 'unknown',
    latency_ms: item.latency_ms ?? null,
    evidence: item.evidence ?? null,
    probe_missing: probes.value.length > 0 && !probeIds.value.has(item.probe || ''),
    local: true,
  }))
})

const checkEditorSeed = computed<CheckDefinition[]>(() => {
  if (checkConfig.value.length) return checkConfig.value
  return checkRows.value.map((row) => ({
    id: row.id,
    name: row.name,
    probe: row.probe,
    params: {},
    expect: {},
    timeout: null,
    depends_on: [],
    enabled: true,
  }))
})

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
  guardError.value = ''
  checkError.value = ''
  try {
    const [detailData, guardData, checkData, probeData] = await Promise.all([
      adminApi.client(clientId.value),
      adminApi.clientGuards(clientId.value).catch((err) => {
        guardError.value = getErrorMessage(err, '守护项配置加载失败')
        return null
      }),
      adminApi.clientChecks(clientId.value).catch((err) => {
        checkError.value = getErrorMessage(err, '检测项配置加载失败')
        return null
      }),
      adminApi.clientProbes(clientId.value).catch((err) => {
        checkError.value = getErrorMessage(err, '探测器目录加载失败')
        return null
      }),
    ])
    detail.value = detailData
    if (guardData) {
      guardConfig.value = Array.isArray(guardData.items) ? guardData.items : []
      guardManaged.value = Boolean(guardData.managed)
    }
    if (checkData) {
      checkConfig.value = Array.isArray(checkData.items) ? checkData.items : []
      checkManaged.value = Boolean(checkData.managed)
    }
    probes.value = Array.isArray(probeData?.items) ? probeData.items : []
    probeUpdatedAt.value = probeData?.updated_at ?? null
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

async function handleCheckSave(payload: CheckDefinition[]) {
  if (checkSaving.value || !clientId.value) return
  checkSaving.value = true
  try {
    const result = await adminApi.saveClientChecks(clientId.value, payload)
    checkConfig.value = result.items
    checkManaged.value = result.managed
    checkEditorOpen.value = false
    ElMessage.success('检测项配置已下发，客户端将在下次配置轮询时生效')
  } catch (err) {
    ElMessage.error(getErrorMessage(err, '下发失败，请检查检测项引用与依赖'))
  } finally {
    checkSaving.value = false
  }
}

async function clearCheckConfig() {
  if (!clientId.value) return
  try {
    await ElMessageBox.confirm(
      '清除检测项会同时清除引用它们的守护项下发配置，客户端将回退本地配置，确定继续？',
      '清除服务端配置',
      { type: 'warning', confirmButtonText: '清除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    if (guardManaged.value) {
      const result = await adminApi.clearClientGuards(clientId.value)
      guardConfig.value = []
      guardManaged.value = result.managed
    }
    const result = await adminApi.saveClientChecks(clientId.value, [])
    checkConfig.value = []
    checkManaged.value = result.managed
    ElMessage.success('已清除服务端检测项与守护项配置')
  } catch (err) {
    ElMessage.error(getErrorMessage(err, '清除失败'))
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

const rotating = ref(false)
const secretDialogOpen = ref(false)
const rotatedSecret = ref('')

async function rotateSecret() {
  if (!clientId.value || rotating.value) return
  try {
    await ElMessageBox.confirm(
      '轮换后旧 secret 立即失效，客户端会认证失败，直到在设备上更新为新 secret。新 secret 只显示一次，确定继续？',
      '轮换客户端 secret',
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
    ElMessage.success('secret 已轮换')
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
          <span class="panel-title">访问凭证</span>
          <el-button size="small" :loading="rotating" @click="rotateSecret">
            轮换 secret
          </el-button>
        </div>
        <div class="panel-body">
          <p class="muted secret-hint">
            secret 仅以哈希形式保存，服务端无法查看明文。轮换后需同步修改设备配置，否则该客户端会认证失败。
          </p>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <span class="panel-title">检测项</span>
          <div class="panel-actions">
            <el-tag v-if="checkManaged" size="small" type="success">已下发</el-tag>
            <el-tag v-else-if="checkRows.length" size="small" type="info">本地配置</el-tag>
            <el-tag v-else size="small" type="info">未下发</el-tag>
            <el-button size="small" type="primary" @click="checkEditorOpen = true">
              编辑配置
            </el-button>
            <el-button
              v-if="checkManaged"
              size="small"
              text
              type="danger"
              @click="clearCheckConfig"
            >
              清除下发
            </el-button>
          </div>
        </div>
        <el-alert
          v-if="checkError"
          class="check-error"
          type="error"
          show-icon
          :closable="false"
          :title="checkError"
        >
          <el-button size="small" text type="primary" @click="load">重试</el-button>
        </el-alert>
        <div class="panel-body panel-body--flush">
          <el-table :data="checkRows" size="small">
            <el-table-column label="名称" min-width="150">
              <template #default="{ row }">
                <span>{{ row.name || row.id }}</span>
                <el-tag v-if="row.local" size="small" type="info">本地</el-tag>
                <el-tag v-if="row.enabled === false" size="small" type="info">停用</el-tag>
                <el-tag v-if="row.probe_missing" size="small" type="danger">probe 缺失</el-tag>
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
            <el-table-column label="依赖" width="130">
              <template #default="{ row }">
                <span class="mono muted">
                  {{ (row.depends_on ?? []).join(', ') || '—' }}
                </span>
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
                title="未下发检测项配置"
                description="客户端当前使用本地检测项；点击“编辑配置”创建并整表下发。"
              >
                <template #action>
                  <el-button size="small" @click="checkEditorOpen = true">新建检测项</el-button>
                </template>
              </EmptyState>
            </template>
          </el-table>
        </div>
        <div class="panel-body">
          <p class="muted check-hint">
            <template v-if="probes.length">
              探测器目录来自最近一次心跳（{{ probes.length }} 项，更新于
              <TimeText :value="probeUpdatedAt" />）。检测项只能引用 remote_allowed 的探测器。
            </template>
            <template v-else>
              客户端尚未上报探测器目录；请确认心跳正常后再配置检测项。
            </template>
            <template v-if="!checkManaged && checkRows.length">
              当前为客户端本地配置，参数不会自动带入，编辑保存前请核对。
            </template>
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
      </div>

      <div class="panel">
        <div class="panel-head">
          <span class="panel-title">守护项配置（下发）</span>
          <div class="panel-actions">
            <el-tag v-if="guardManaged" size="small" type="success">已下发</el-tag>
            <el-tag v-else size="small" type="info">未下发</el-tag>
            <el-button
              size="small"
              type="primary"
              :disabled="!checkConfig.length"
              @click="openGuardEditor('')"
            >
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
                <span class="mono">{{ (row.check_refs ?? []).length }}</span>
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
            守护项只能引用上方已保存的检测项；保存后客户端在下一次配置轮询（默认 ≤ 5 分钟）自动生效。
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

    <CheckEditor
      v-model="checkEditorOpen"
      :checks="checkEditorSeed"
      :probes="probes"
      :busy="checkSaving"
      @save="handleCheckSave"
    />

    <GuardEditor
      v-model="guardEditorOpen"
      :guards="guardConfig"
      :checks="checkConfig"
      :busy="guardSaving"
      :selected-id="guardEditorSelected"
      title="配置下发守护项"
      hint="引用上方检测项；保存后写入服务端并通过 /api/v1/config 下发，客户端本地配置将变为只读。"
      @save="handleGuardSave"
    />

    <el-dialog
      v-model="secretDialogOpen"
      title="新的客户端 secret"
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
