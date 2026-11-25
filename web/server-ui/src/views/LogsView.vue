<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  EmptyState,
  LoadingBlock,
  StatusTag,
  TimeText,
  getErrorMessage,
  unwrapList,
  copyText,
  type ServerLogEntry,
  type ServerLogQuery,
} from '@vigil/shared'
import { ElMessage } from 'element-plus'
import { adminApi } from '@/api/admin'

const RANGES = [
  { label: '近 15 分钟', value: '15m', seconds: 15 * 60 },
  { label: '近 1 小时', value: '1h', seconds: 60 * 60 },
  { label: '近 6 小时', value: '6h', seconds: 6 * 60 * 60 },
  { label: '近 24 小时', value: '24h', seconds: 24 * 60 * 60 },
  { label: '近 7 天', value: '7d', seconds: 7 * 24 * 60 * 60 },
  { label: '全部', value: 'all', seconds: 0 },
]

const EVENT_HINTS = [
  'api.request',
  'auth.login.',
  'client.register',
  'guard_config.update',
  'incident.open',
  'incident.recover',
  'notify.send',
  'notify.fail',
  'server.error',
]

const logs = ref<ServerLogEntry[]>([])
const loading = ref(false)
const error = ref('')

const level = ref('')
const instance = ref('')
const event = ref('')
const clientId = ref('')
const requestId = ref('')
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

function queryEventHints(
  query: string,
  callback: (items: Array<{ value: string }>) => void,
) {
  const keyword = query.trim().toLowerCase()
  callback(
    EVENT_HINTS.filter((hint) => !keyword || hint.toLowerCase().includes(keyword)).map(
      (value) => ({ value }),
    ),
  )
}

function levelStatus(value: string): string {
  const normalized = String(value || '').toLowerCase()
  if (normalized === 'error' || normalized === 'critical') return 'CRITICAL'
  if (normalized === 'warn' || normalized === 'warning') return 'WARN'
  if (normalized === 'debug') return 'UNKNOWN'
  return 'OK'
}

function levelText(entry: ServerLogEntry): string {
  return String(entry.level || 'info').toUpperCase()
}

function buildQuery(includeLimit = true): ServerLogQuery {
  const selected = RANGES.find((item) => item.value === range.value)
  const query: ServerLogQuery = {}
  if (level.value) query.level = level.value
  if (instance.value) query.instance = instance.value
  if (event.value.trim()) query.event = event.value.trim()
  if (clientId.value.trim()) query.client_id = clientId.value.trim()
  if (requestId.value.trim()) query.request_id = requestId.value.trim()
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
    logs.value = unwrapList(await adminApi.logs(buildQuery()))
  } catch (err) {
    error.value = getErrorMessage(err, '服务端日志加载失败')
  } finally {
    loading.value = false
  }
}

function exportLogs() {
  const anchor = document.createElement('a')
  anchor.href = adminApi.logsExportUrl(buildQuery(false))
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}

async function copy(value?: string) {
  if (!value) return
  if (await copyText(value)) {
    ElMessage.success('已复制')
  } else {
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

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">服务端日志</h2>
        <p class="page-sub">共 {{ logs.length }} 条 · 合并 api / worker 两个实例</p>
      </div>
      <div class="head-actions">
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
          <label class="field field--short">
            <span class="field-label">实例</span>
            <el-select v-model="instance" size="small" clearable @change="load">
              <el-option label="全部" value="" />
              <el-option label="api" value="api" />
              <el-option label="worker" value="worker" />
            </el-select>
          </label>
          <label class="field">
            <span class="field-label">事件前缀</span>
            <el-autocomplete
              v-model="event"
              size="small"
              clearable
              :fetch-suggestions="queryEventHints"
              placeholder="如 incident. 或 notify."
              @keyup.enter="load"
              @clear="load"
              @select="load"
            />
          </label>
          <label class="field">
            <span class="field-label">客户端</span>
            <el-input
              v-model="clientId"
              size="small"
              clearable
              placeholder="client_id"
              @keyup.enter="load"
              @clear="load"
            />
          </label>
          <label class="field">
            <span class="field-label">请求 ID</span>
            <el-input
              v-model="requestId"
              size="small"
              clearable
              placeholder="request_id"
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

    <LoadingBlock v-if="loading && !logs.length" min-height="180px" text="正在读取服务端日志…" />

    <EmptyState
      v-else-if="!logs.length && !loading"
      title="暂无服务端日志"
      description="当前过滤条件下没有记录，可放宽时间范围或清空条件。"
    >
      <template #action>
        <el-button size="small" @click="load">重新加载</el-button>
      </template>
    </EmptyState>

    <div v-else class="panel">
      <div class="panel-body panel-body--flush">
        <ul class="list log-list">
          <li
            v-for="(entry, index) in sortedLogs"
            :key="`${entry.instance || 'server'}-${entry.seq ?? index}-${entry.ts}`"
            class="log-item"
          >
            <div class="log-head">
              <div class="log-title">
                <StatusTag :status="levelStatus(entry.level)" :label="levelText(entry)" size="sm" />
                <span v-if="entry.instance" class="badge mono">{{ entry.instance }}</span>
                <span class="mono event-name">{{ entry.event }}</span>
              </div>
              <TimeText :value="entry.ts" mode="datetime" class="log-time" />
            </div>

            <p class="log-msg">{{ entry.msg }}</p>

            <div class="log-meta">
              <span v-if="entry.method" class="mono">
                {{ entry.method }} {{ entry.path }}
                <template v-if="entry.status"> · {{ entry.status }}</template>
              </span>
              <span v-if="entry.duration_ms !== undefined && entry.duration_ms !== null">
                耗时 {{ entry.duration_ms }} ms
              </span>
              <span v-if="entry.actor">操作人 {{ entry.actor }}</span>
              <span v-if="entry.client_id" class="mono">client {{ entry.client_id }}</span>
              <span v-if="entry.incident_id">incident #{{ entry.incident_id }}</span>
              <span v-if="entry.channel">{{ entry.channel }} / {{ entry.kind }}</span>
              <button
                v-if="entry.request_id"
                class="linkish mono"
                type="button"
                @click="copy(entry.request_id)"
              >
                request {{ entry.request_id }}
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
  min-width: 140px;

  &--short {
    min-width: 90px;
    max-width: 110px;
  }
}

.field-label {
  color: var(--vigil-color-text-muted);
  font-size: var(--vigil-font-sm);
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

.badge {
  padding: 0 6px;
  border: 1px solid var(--vigil-color-border);
  border-radius: var(--vigil-radius-sm);
  color: var(--vigil-color-text-muted);
  font-size: var(--vigil-font-xxs);
}

.event-name {
  font-size: var(--vigil-font-sm);
  font-weight: 600;
  word-break: break-all;
}

.log-time {
  flex: 0 0 auto;
  color: var(--vigil-color-text-muted);
  font-size: var(--vigil-font-sm);
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
  font-size: var(--vigil-font-sm);
  word-break: break-all;
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
    font-size: var(--vigil-font-sm);
  }

  pre {
    max-height: 260px;
    margin: var(--vigil-space-2) 0 0;
    padding: var(--vigil-space-2);
    overflow: auto;
    background: var(--vigil-color-surface-muted);
    border-radius: var(--vigil-radius-sm);
    font-size: 12px;
    white-space: pre-wrap;
    word-break: break-all;
  }
}
</style>
