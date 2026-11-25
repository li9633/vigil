<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  EmptyState,
  LoadingBlock,
  StatusTag,
  TimeText,
  getErrorMessage,
  unwrapList,
  type LogEntry,
  type LogQuery,
} from '@vigil/shared'
import { ElMessage } from 'element-plus'
import { clientApi } from '@/api/client'

const RANGES = [
  { label: '近 15 分钟', value: '15m', seconds: 15 * 60 },
  { label: '近 1 小时', value: '1h', seconds: 60 * 60 },
  { label: '近 6 小时', value: '6h', seconds: 6 * 60 * 60 },
  { label: '近 24 小时', value: '24h', seconds: 24 * 60 * 60 },
  { label: '近 7 天', value: '7d', seconds: 7 * 24 * 60 * 60 },
  { label: '全部', value: 'all', seconds: 0 },
]

const EVENT_HINTS = [
  'guard.state',
  'guard.check',
  'guard.recover.start',
  'guard.recover.verify',
  'guard.recover.result',
  'guard.recover.give_up',
  'action.result',
  'heartbeat.fail',
  'config.fetch.fail',
  'guard_config.apply',
  'settings.update',
  'probe.error',
]

const logs = ref<LogEntry[]>([])
const guards = ref<{ id: string; name: string }[]>([])
const loading = ref(false)
const error = ref('')

const level = ref('')
const event = ref('')
const guardId = ref('')
const recoveryId = ref('')
const range = ref('1h')
const limit = ref(200)

const levelOptions = [
  { label: '全部级别', value: '' },
  { label: 'DEBUG 及以上', value: 'debug' },
  { label: 'INFO 及以上', value: 'info' },
  { label: 'WARN 及以上', value: 'warn' },
  { label: 'ERROR', value: 'error' },
]

const sortedLogs = computed(() => [...logs.value].reverse())

function levelStatus(value: string): string {
  const normalized = String(value || '').toLowerCase()
  if (normalized === 'error' || normalized === 'critical') return 'CRITICAL'
  if (normalized === 'warn' || normalized === 'warning') return 'WARN'
  if (normalized === 'debug') return 'UNKNOWN'
  return 'OK'
}

function levelText(entry: LogEntry): string {
  return String(entry.level || 'info').toUpperCase()
}

function guardName(entry: LogEntry): string {
  const id = entry.guard?.id || ''
  if (!id) return '—'
  return guards.value.find((item) => item.id === id)?.name || entry.guard?.name || id
}

function buildQuery(includeLimit = true): LogQuery {
  const selected = RANGES.find((item) => item.value === range.value)
  const query: LogQuery = {}
  if (level.value) query.level = level.value
  if (event.value.trim()) query.event = event.value.trim()
  if (guardId.value) query.guard_id = guardId.value
  if (recoveryId.value.trim()) query.recovery_id = recoveryId.value.trim()
  if (selected && selected.seconds > 0) {
    query.since = Math.floor(Date.now() / 1000) - selected.seconds
  }
  if (includeLimit) query.limit = limit.value
  return query
}

async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    logs.value = unwrapList(await clientApi.logs(buildQuery()))
  } catch (err) {
    error.value = getErrorMessage(err, '日志加载失败')
  } finally {
    loading.value = false
  }
}

async function loadGuards() {
  try {
    const snapshot = await clientApi.status()
    guards.value = (snapshot.guards || []).map((guard) => ({
      id: guard.id,
      name: guard.name || guard.id,
    }))
  } catch {
    guards.value = []
  }
}

function exportLogs() {
  const url = clientApi.logsExportUrl(buildQuery(false))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}

async function copy(value?: string) {
  if (!value) return
  try {
    await navigator.clipboard.writeText(value)
    ElMessage.success('已复制')
  } catch {
    ElMessage.warning('复制失败，请手动选择')
  }
}

function pretty(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

onMounted(() => {
  loadGuards()
  load()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">日志</h2>
        <p class="page-sub">共 {{ logs.length }} 条 · 本机结构化日志，导出为 JSONL</p>
      </div>
      <div class="head-actions">
        <el-button size="small" :loading="loading" @click="load">刷新</el-button>
        <el-button size="small" type="primary" plain @click="exportLogs">导出</el-button>
      </div>
    </div>

    <div class="panel">
      <div class="panel-body">
        <div class="filters">
          <label class="field">
            <span class="field-label">级别</span>
            <el-select v-model="level" size="small" @change="load">
              <el-option
                v-for="option in levelOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
          </label>
          <label class="field">
            <span class="field-label">守护项</span>
            <el-select v-model="guardId" size="small" clearable filterable @change="load">
              <el-option label="全部守护项" value="" />
              <el-option
                v-for="guard in guards"
                :key="guard.id"
                :label="guard.name"
                :value="guard.id"
              />
            </el-select>
          </label>
          <label class="field">
            <span class="field-label">事件前缀</span>
            <el-input
              v-model="event"
              size="small"
              clearable
              placeholder="如 guard. 或 action.result"
              list="log-event-hints"
              @keyup.enter="load"
              @clear="load"
            />
            <datalist id="log-event-hints">
              <option v-for="hint in EVENT_HINTS" :key="hint" :value="hint" />
            </datalist>
          </label>
          <label class="field">
            <span class="field-label">恢复 ID</span>
            <el-input
              v-model="recoveryId"
              size="small"
              clearable
              placeholder="recovery_id"
              @keyup.enter="load"
              @clear="load"
            />
          </label>
          <label class="field">
            <span class="field-label">时间范围</span>
            <el-select v-model="range" size="small" @change="load">
              <el-option
                v-for="option in RANGES"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
          </label>
          <label class="field field--short">
            <span class="field-label">条数</span>
            <el-select v-model="limit" size="small" @change="load">
              <el-option :value="100" label="100" />
              <el-option :value="200" label="200" />
              <el-option :value="500" label="500" />
            </el-select>
          </label>
          <el-button size="small" type="primary" :loading="loading" @click="load">查询</el-button>
        </div>
      </div>
    </div>

    <el-alert v-if="error" class="error-box" type="error" show-icon :closable="false">
      <div class="alert-row">
        <span>{{ error }}</span>
        <el-button size="small" text type="primary" @click="load">重试</el-button>
      </div>
    </el-alert>

    <LoadingBlock v-if="loading && !logs.length" min-height="180px" text="正在读取日志…" />

    <EmptyState
      v-else-if="!logs.length && !loading"
      title="暂无日志"
      description="当前过滤条件下没有记录，可放宽时间范围或清空过滤条件。"
    >
      <template #action>
        <el-button size="small" @click="load">重新加载</el-button>
      </template>
    </EmptyState>

    <div v-else class="panel">
      <div class="panel-body panel-body--flush">
        <ul class="list log-list">
          <li v-for="(entry, index) in sortedLogs" :key="entry.seq ?? `${entry.ts}-${index}`" class="log-item">
            <div class="log-head">
              <div class="log-title">
                <StatusTag :status="levelStatus(entry.level)" :label="levelText(entry)" size="sm" />
                <span class="mono event-name">{{ entry.event }}</span>
                <span v-if="entry.guard?.id" class="guard-name">{{ guardName(entry) }}</span>
              </div>
              <TimeText :value="entry.ts" mode="datetime" class="log-time" />
            </div>

            <p class="log-msg">{{ entry.msg }}</p>

            <div class="log-meta">
              <span v-if="entry.action?.name">
                动作 <span class="mono">{{ entry.action.name }}</span>
                <template v-if="entry.action.target">· {{ entry.action.target }}</template>
                <template v-if="entry.action.result">· {{ entry.action.result }}</template>
              </span>
              <span v-if="entry.duration_ms !== undefined && entry.duration_ms !== null">
                耗时 {{ entry.duration_ms }} ms
              </span>
              <span v-if="entry.attempt">第 {{ entry.attempt }} 次尝试</span>
              <span v-if="entry.suppressed">已抑制 {{ entry.suppressed }} 条</span>
              <button v-if="entry.trace_id" class="linkish mono" type="button" @click="copy(entry.trace_id)">
                trace {{ entry.trace_id.slice(0, 8) }}…
              </button>
              <button
                v-if="entry.recovery_id"
                class="linkish mono"
                type="button"
                @click="copy(entry.recovery_id)"
              >
                recovery {{ entry.recovery_id.slice(0, 8) }}…
              </button>
            </div>

            <details v-if="entry.evidence || entry.err" class="log-detail">
              <summary>查看详情</summary>
              <pre v-if="entry.evidence" class="mono">{{ pretty(entry.evidence) }}</pre>
              <pre v-if="entry.err?.stack" class="mono">{{ entry.err.stack }}</pre>
            </details>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.alert-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--vigil-space-2);
}

.head-actions {
  display: flex;
  gap: var(--vigil-space-2);
}

.filters {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--vigil-space-3);
}

.field {
  display: flex;
  flex-direction: column;
  gap: var(--vigil-space-1);
  min-width: 160px;

  &--short {
    min-width: 90px;
    max-width: 110px;
  }
}

.field-label {
  color: var(--vigil-color-text-muted);
  font-size: var(--vigil-font-size-sm);
}

.log-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.log-item {
  padding: var(--vigil-space-3) var(--vigil-space-4);
  border-bottom: 1px solid var(--vigil-color-border);

  &:last-child {
    border-bottom: none;
  }
}

.log-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--vigil-space-3);
}

.log-title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--vigil-space-2);
  min-width: 0;
}

.event-name {
  font-size: var(--vigil-font-size-sm);
  font-weight: 600;
  word-break: break-all;
}

.guard-name {
  padding: 0 6px;
  border: 1px solid var(--vigil-color-border);
  border-radius: var(--vigil-radius-sm);
  color: var(--vigil-color-text-muted);
  font-size: var(--vigil-font-size-sm);
}

.log-time {
  flex: 0 0 auto;
  color: var(--vigil-color-text-muted);
  font-size: var(--vigil-font-size-sm);
}

.log-msg {
  margin: var(--vigil-space-1) 0 0;
  word-break: break-word;
}

.log-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--vigil-space-3);
  margin-top: var(--vigil-space-1);
  color: var(--vigil-color-text-muted);
  font-size: var(--vigil-font-size-sm);
}

.linkish {
  padding: 0;
  border: none;
  background: none;
  color: var(--vigil-color-text-muted);
  cursor: pointer;
  font-size: inherit;
  text-decoration: underline dotted;

  &:hover {
    color: var(--vigil-color-text);
  }
}

.log-detail {
  margin-top: var(--vigil-space-2);

  summary {
    color: var(--vigil-color-text-muted);
    cursor: pointer;
    font-size: var(--vigil-font-size-sm);
  }

  pre {
    max-height: 260px;
    margin: var(--vigil-space-2) 0 0;
    padding: var(--vigil-space-2);
    overflow: auto;
    background: var(--vigil-color-surface-muted, rgba(127, 127, 127, 0.08));
    border-radius: var(--vigil-radius-sm);
    font-size: 12px;
    white-space: pre-wrap;
    word-break: break-all;
  }
}
</style>
