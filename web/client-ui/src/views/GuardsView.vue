<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  EmptyState,
  GuardEditor,
  LiveBadge,
  LoadingBlock,
  RefreshFab,
  StatusTag,
  formatLatency,
  formatRelative,
  getErrorMessage,
  usePolling,
  type CheckOption,
  type GuardCheckResult,
  type GuardDefinition,
  type GuardSnapshot,
} from '@vigil/shared'
import { clientApi } from '@/api/client'

const definitions = ref<GuardDefinition[]>([])
const snapshots = ref<GuardSnapshot[]>([])
const checkOptions = ref<CheckOption[]>([])
const source = ref<string>('local')
const editable = ref(true)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const now = ref(Date.now())

const editorOpen = ref(false)
const editorReadonly = ref(false)
const editorSelected = ref('')
const editorTitle = ref('编辑守护项')
const editorHint = ref('')

interface GuardRow {
  def: GuardDefinition
  snap: GuardSnapshot | null
}

const rows = computed<GuardRow[]>(() => {
  const byId = new Map(snapshots.value.map((item) => [item.id, item]))
  return [...definitions.value]
    .sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)))
    .map((def) => ({ def, snap: byId.get(def.id) ?? null }))
})

const abnormalCount = computed(
  () =>
    rows.value.filter(({ snap }) => {
      const status = String(snap?.status || '')
      return status !== 'OK' && status !== 'DISABLED' && status !== ''
    }).length,
)

const sourceLabel = computed(() =>
  source.value === 'server' ? '服务端托管' : '本地配置',
)

function checksOf(snap: GuardSnapshot | null): GuardCheckResult[] {
  return Object.values(snap?.checks ?? {}).sort((a, b) =>
    String(a.id).localeCompare(String(b.id)),
  )
}

function evidenceText(evidence: Record<string, unknown> | null | undefined): string {
  if (!evidence) return '—'
  const parts = Object.entries(evidence)
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .slice(0, 4)
    .map(([key, value]) => `${key}=${typeof value === 'object' ? JSON.stringify(value) : String(value)}`)
  return parts.length ? parts.join(' · ') : '—'
}

function rearmText(snap: GuardSnapshot | null): string {
  if (!snap || String(snap.status || '') !== 'GAVE_UP' || !snap.rearm_at) return ''
  return `下次重试 ${formatRelative(snap.rearm_at, now.value)}`
}

function openEditor(selectedId = '', readonly = false): void {
  editorSelected.value = selectedId
  editorReadonly.value = readonly
  editorTitle.value = readonly ? '查看守护项配置' : '编辑守护项'
  editorHint.value = readonly
    ? '当前配置由服务端统一管理，客户端只读；请到服务端管理台修改。'
    : '修改后立即生效并写入本地配置；检测与恢复动作按新策略执行。'
  editorOpen.value = true
}

async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const [config, status, checks] = await Promise.all([
      clientApi.guards(),
      clientApi.status(),
      clientApi.checks(),
    ])
    definitions.value = Array.isArray(config.items) ? config.items : []
    source.value = config.source || 'local'
    editable.value = config.editable !== false
    snapshots.value = Array.isArray(status.guards) ? status.guards : []
    const checkItems = Array.isArray(checks) ? checks : checks.items ?? []
    checkOptions.value = checkItems.map((item) => ({
      id: item.id,
      name: item.name,
      probe: item.probe,
      enabled: item.enabled,
      depends_on: item.depends_on,
    }))
    now.value = Date.now()
  } catch (err) {
    error.value = getErrorMessage(err, '守护项加载失败')
  } finally {
    loading.value = false
  }
}

async function handleSave(payload: GuardDefinition[]) {
  if (saving.value) return
  saving.value = true
  try {
    const result = await clientApi.saveGuards(payload)
    definitions.value = result.items
    source.value = result.source
    editable.value = result.editable
    editorOpen.value = false
    ElMessage.success('守护项配置已保存并生效')
    await load()
  } catch (err) {
    ElMessage.error(getErrorMessage(err, '保存失败，请检查配置'))
  } finally {
    saving.value = false
  }
}

const poll = usePolling(load, {
  interval: 10000,
  isError: () => Boolean(error.value),
})
</script>

<template>
  <div class="page pb-fab">
    <div class="page-head">
      <div>
        <h2 class="page-title">守护项</h2>
        <p class="page-sub">
          共 {{ definitions.length }} 项 · 异常
          <span :class="{ 'text-critical': abnormalCount > 0 }">{{ abnormalCount }}</span> 项 ·
          {{ sourceLabel }}
        </p>
      </div>
      <div class="head-actions">
        <LiveBadge :at="poll.lastUpdated" :paused="poll.paused" />
        <el-button
          v-if="editable"
          size="small"
          type="primary"
          @click="openEditor('')"
        >
          编辑配置
        </el-button>
        <el-button v-else size="small" @click="openEditor('', true)">查看配置</el-button>
      </div>
    </div>

    <el-alert
      v-if="source === 'server'"
      class="source-alert"
      type="info"
      show-icon
      :closable="false"
      title="守护项由服务端统一管理"
      description="客户端本地为只读；服务端修改后会在下一次配置同步时自动生效。"
    />

    <el-alert v-if="error" class="error-box" type="error" show-icon :closable="false">
      <div class="alert-row">
        <span>{{ error }}</span>
        <el-button size="small" text type="primary" @click="poll.retry">重试</el-button>
      </div>
    </el-alert>

    <LoadingBlock v-if="loading && !definitions.length" min-height="180px" text="正在读取守护项…" />

    <EmptyState
      v-else-if="!definitions.length && !loading"
      title="暂无守护项"
      description="尚未配置守护项，可新建一个检测与恢复单元。"
    >
      <template #action>
        <el-button v-if="editable" size="small" type="primary" @click="openEditor('')">
          新增守护项
        </el-button>
        <el-button v-else size="small" @click="load">重新加载</el-button>
      </template>
    </EmptyState>

    <template v-else>
      <div v-for="row in rows" :key="row.def.id" class="panel guard-card">
        <div class="panel-head guard-head">
          <div class="guard-title">
            <span class="guard-name">{{ row.def.name || row.def.id }}</span>
            <span v-if="row.def.template" class="mono template-tag">{{ row.def.template }}</span>
            <span v-if="row.def.enabled === false" class="mono muted">已停用</span>
          </div>
          <div class="guard-status">
            <StatusTag v-if="row.snap" :status="row.snap.status" size="sm" />
            <span v-else class="muted wait-tag">等待首轮检测</span>
            <span v-if="row.snap" class="check-status muted">
              检测 <StatusTag :status="row.snap.check_status || 'UNKNOWN'" size="sm" />
            </span>
            <el-button
              size="small"
              text
              type="primary"
              @click="openEditor(row.def.id, !editable)"
            >
              {{ editable ? '编辑' : '查看' }}
            </el-button>
          </div>
        </div>
        <div class="panel-body">
          <dl class="meta-grid">
            <div class="meta-item">
              <dt>检测间隔</dt>
              <dd class="mono">{{ row.def.policy?.interval ?? 30 }}s</dd>
            </div>
            <div class="meta-item">
              <dt>连续失败</dt>
              <dd class="mono">{{ row.snap?.failure_count ?? 0 }}</dd>
            </div>
            <div class="meta-item">
              <dt>连续成功</dt>
              <dd class="mono">{{ row.snap?.success_count ?? 0 }}</dd>
            </div>
            <div class="meta-item">
              <dt>恢复轮次</dt>
              <dd class="mono">{{ row.snap?.recovery_rounds ?? 0 }}</dd>
            </div>
            <div class="meta-item">
              <dt>重新武装</dt>
              <dd class="mono">{{ rearmText(row.snap) || '—' }}</dd>
            </div>
            <div class="meta-item wide">
              <dt>守护项 ID</dt>
              <dd class="mono">{{ row.def.id }}</dd>
            </div>
          </dl>

          <ul v-if="checksOf(row.snap).length" class="check-list">
            <li v-for="check in checksOf(row.snap)" :key="check.id" class="check-item">
              <div class="check-main">
                <span class="mono check-name">{{ check.id }}</span>
                <span v-if="check.probe" class="mono probe-tag">{{ check.probe }}</span>
                <span class="check-evidence muted">{{ evidenceText(check.evidence) }}</span>
              </div>
              <div class="check-side">
                <StatusTag :status="check.status" size="sm" />
                <span class="mono muted">{{ formatLatency(check.latency_ms) }}</span>
              </div>
            </li>
          </ul>
          <p v-else class="muted empty-checks">该守护项暂无检测结果</p>
        </div>
      </div>
    </template>

    <GuardEditor
      v-model="editorOpen"
      :guards="definitions"
      :checks="checkOptions"
      :readonly="editorReadonly"
      :busy="saving"
      :selected-id="editorSelected"
      :title="editorTitle"
      :hint="editorHint"
      @save="handleSave"
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
.head-actions {
  display: flex;
  gap: var(--vigil-space-2);
}

.source-alert {
  margin-bottom: var(--vigil-space-3);
}

.alert-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--vigil-space-2);
}

.guard-card {
  margin-bottom: var(--vigil-space-4);
}

.guard-head {
  gap: var(--vigil-space-2);
  flex-wrap: wrap;
}

.guard-title {
  display: flex;
  align-items: center;
  gap: var(--vigil-space-2);
  min-width: 0;
}

.guard-name {
  font-size: var(--vigil-font-sm);
  font-weight: 600;
}

.template-tag,
.probe-tag {
  padding: 0 6px;
  border: 1px solid var(--vigil-color-border);
  border-radius: var(--vigil-radius-sm);
  font-size: 11px;
  line-height: 18px;
  color: var(--vigil-color-text-muted);
}

.guard-status {
  display: flex;
  align-items: center;
  gap: var(--vigil-space-2);
}

.check-status {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--vigil-font-xs);
}

.wait-tag {
  font-size: 11px;
}

.meta-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--vigil-space-3) var(--vigil-space-4);
  margin: 0 0 var(--vigil-space-3);
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

.check-list {
  margin: 0;
  padding: 0;
  list-style: none;
  border-top: 1px solid var(--vigil-color-border);
}

.check-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--vigil-space-3);
  padding: 8px 0;
  border-bottom: 1px solid var(--vigil-color-border);

  &:last-child {
    border-bottom: none;
  }
}

.check-main {
  display: flex;
  align-items: center;
  gap: var(--vigil-space-2);
  min-width: 0;
}

.check-name {
  flex: 0 0 auto;
  font-size: var(--vigil-font-xs);
}

.check-evidence {
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.check-side {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: var(--vigil-space-2);
  font-size: var(--vigil-font-xs);
}

.empty-checks {
  margin: 0;
  font-size: var(--vigil-font-xs);
}
</style>
