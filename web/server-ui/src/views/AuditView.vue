<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  EmptyState,
  TimeText,
  getErrorMessage,
  type AuditQuery,
  type AuditRecord,
} from '@vigil/shared'
import { adminApi } from '@/api/admin'

const RANGES = [
  { label: '近 1 小时', value: '1h', seconds: 60 * 60 },
  { label: '近 6 小时', value: '6h', seconds: 6 * 60 * 60 },
  { label: '近 24 小时', value: '24h', seconds: 24 * 60 * 60 },
  { label: '近 7 天', value: '7d', seconds: 7 * 24 * 60 * 60 },
  { label: '近 30 天', value: '30d', seconds: 30 * 24 * 60 * 60 },
  { label: '全部', value: 'all', seconds: 0 },
]

const ACTION_HINTS = [
  'login',
  'login_fail',
  'login_blocked',
  'ack_incident',
  'update_notifications',
  'test_notification',
  'guards_update',
  'guards_clear',
  'update_policies',
]

const ACTION_LABELS: Record<string, string> = {
  login: '登录',
  login_fail: '登录失败',
  login_blocked: '登录限流',
  ack_incident: '确认告警',
  update_notifications: '更新通知配置',
  test_notification: '测试通知',
  guards_update: '更新守护项配置',
  guards_clear: '清除守护项配置',
  update_policies: '更新策略配置',
}

const records = ref<AuditRecord[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref('')

const actor = ref('')
const action = ref('')
const target = ref('')
const range = ref('24h')
const limit = ref(200)

function queryActionHints(
  query: string,
  callback: (items: Array<{ value: string }>) => void,
) {
  const keyword = query.trim().toLowerCase()
  callback(
    ACTION_HINTS.filter((hint) => !keyword || hint.includes(keyword)).map((value) => ({
      value,
    })),
  )
}

function buildQuery(includeLimit = true): AuditQuery {
  const selected = RANGES.find((item) => item.value === range.value)
  const query: AuditQuery = {}
  if (actor.value.trim()) query.actor = actor.value.trim()
  if (action.value.trim()) query.action = action.value.trim()
  if (target.value.trim()) query.target = target.value.trim()
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
    const data = await adminApi.audit(buildQuery())
    records.value = data.items ?? []
    total.value = data.total ?? records.value.length
  } catch (err) {
    error.value = getErrorMessage(err, '审计日志加载失败')
  } finally {
    loading.value = false
  }
}

function exportAudit() {
  const anchor = document.createElement('a')
  anchor.href = adminApi.auditExportUrl(buildQuery(false))
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}

function actionLabel(value: string | null | undefined): string {
  if (!value) return '—'
  return ACTION_LABELS[value] ?? value
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">审计日志</h2>
        <p class="page-sub">共 {{ total }} 条 · 记录登录、配置变更与手动操作</p>
      </div>
      <div class="head-actions">
        <el-button size="small" type="primary" plain @click="exportAudit">导出</el-button>
      </div>
    </div>

    <div class="panel">
      <div class="panel-body">
        <div class="filters">
          <label class="field">
            <span class="field-label">操作者</span>
            <el-input
              v-model="actor"
              size="small"
              clearable
              placeholder="如 admin"
              @keyup.enter="load"
              @clear="load"
            />
          </label>
          <label class="field">
            <span class="field-label">操作</span>
            <el-autocomplete
              v-model="action"
              size="small"
              clearable
              :fetch-suggestions="queryActionHints"
              placeholder="如 login / guards_"
              @keyup.enter="load"
              @clear="load"
              @select="load"
            />
          </label>
          <label class="field">
            <span class="field-label">对象</span>
            <el-input
              v-model="target"
              size="small"
              clearable
              placeholder="如 client_id"
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

    <div class="panel">
      <div class="panel-body panel-body--flush">
        <el-table v-loading="loading" :data="records" row-key="id">
          <el-table-column label="时间" width="170">
            <template #default="{ row }">
              <TimeText :value="row.ts" mode="datetime" />
            </template>
          </el-table-column>
          <el-table-column label="操作者" width="140" prop="actor" />
          <el-table-column label="操作" width="150">
            <template #default="{ row }">{{ actionLabel(row.action) }}</template>
          </el-table-column>
          <el-table-column label="对象" min-width="160">
            <template #default="{ row }">
              <span class="mono">{{ row.target || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="详情" min-width="280">
            <template #default="{ row }">
              <span class="mono detail" :title="row.detail">{{ row.detail || '—' }}</span>
            </template>
          </el-table-column>
          <template #empty>
            <EmptyState
              title="暂无审计记录"
              description="当前过滤条件下没有记录，可放宽时间范围或清空条件。"
            />
          </template>
        </el-table>
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
  min-width: 150px;

  &--short {
    min-width: 90px;
    max-width: 110px;
  }
}

.field-label {
  color: var(--vigil-color-text-muted);
  font-size: var(--vigil-font-sm);
}

.detail {
  display: block;
  max-width: 520px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
