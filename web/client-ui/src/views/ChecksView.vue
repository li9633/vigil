<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  EmptyState,
  LiveBadge,
  LoadingBlock,
  RefreshFab,
  StatusTag,
  TimeText,
  formatLatency,
  getErrorMessage,
  usePolling,
  type CheckItem,
  type CheckTestResponse,
} from '@vigil/shared'
import { clientApi } from '@/api/client'

const checks = ref<CheckItem[]>([])
const loading = ref(false)
const error = ref('')
const testingId = ref('')

const testOpen = ref(false)
const testTitle = ref('')
const testResult = ref<CheckTestResponse | null>(null)

const sorted = computed(() =>
  [...checks.value].sort((a, b) => {
    const left = `${a.layer ?? ''}${a.id}`
    const right = `${b.layer ?? ''}${b.id}`
    return left.localeCompare(right)
  }),
)

const abnormalCount = computed(
  () => checks.value.filter((item) => item.has_result && item.status !== 'ok').length,
)

function statusOf(check: CheckItem): string {
  if (check.probe_missing) return 'unknown'
  return check.enabled === false ? 'DISABLED' : check.status
}

function jsonText(value?: Record<string, unknown> | null): string {
  if (!value || !Object.keys(value).length) return '—'
  const text = JSON.stringify(value)
  return text.length > 120 ? `${text.slice(0, 120)}…` : text
}

function dependsText(check: CheckItem): string {
  const deps = check.depends_on ?? []
  return deps.length ? deps.join(', ') : '—'
}

async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const payload = await clientApi.checks()
    checks.value = Array.isArray(payload) ? payload : payload.items ?? []
  } catch (err) {
    error.value = getErrorMessage(err, '检测项加载失败')
  } finally {
    loading.value = false
  }
}

async function runTest(check: CheckItem) {
  if (testingId.value) return
  testingId.value = check.id
  try {
    testResult.value = await clientApi.testCheck(check.id)
    testTitle.value = check.name || check.id
    testOpen.value = true
  } catch (err) {
    ElMessage.error(getErrorMessage(err, '试运行失败'))
  } finally {
    testingId.value = ''
  }
}

function pretty(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2)
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
        <h2 class="page-title">检测项</h2>
        <p class="page-sub">
          共 {{ checks.length }} 项 · 异常
          <span :class="{ 'text-critical': abnormalCount > 0 }">{{ abnormalCount }}</span> 项
        </p>
      </div>
      <LiveBadge :at="poll.lastUpdated" :paused="poll.paused" />
    </div>

    <el-alert v-if="error" class="error-box" type="error" show-icon :closable="false">
      <div class="alert-row">
        <span>{{ error }}</span>
        <el-button size="small" text type="primary" @click="poll.retry">重试</el-button>
      </div>
    </el-alert>

    <LoadingBlock v-if="loading && !checks.length" min-height="180px" text="正在读取检测项…" />

    <EmptyState
      v-else-if="!checks.length && !loading"
      title="暂无检测项"
      description="尚未配置检测项，或接口暂无返回。"
    >
      <template #action>
        <el-button size="small" @click="poll.retry">重新加载</el-button>
      </template>
    </EmptyState>

    <div v-else class="panel">
      <div class="panel-body panel-body--flush">
        <ul class="list">
          <li v-for="check in sorted" :key="check.id" class="list-item">
            <div class="list-main">
              <p class="list-title">
                {{ check.name || check.id }}
                <span class="mono tag">{{ check.probe || '—' }}</span>
                <span v-if="check.probe_missing" class="mono tag tag--critical">probe 缺失</span>
                <span v-if="check.enabled === false" class="mono tag">已停用</span>
              </p>
              <p class="list-meta">{{ check.evidence || '暂无证据' }}</p>
              <p class="list-meta mono">
                参数 {{ jsonText(check.params) }}
              </p>
              <p class="list-meta mono">
                期望 {{ jsonText(check.expect) }}
              </p>
              <p class="list-meta">
                依赖 {{ dependsText(check) }} · 超时 {{ check.timeout ?? '—' }}s · 间隔
                {{ check.interval ?? '—' }}s
                <template v-if="check.updated_at">
                  · 更新于 <TimeText :value="check.updated_at" />
                </template>
              </p>
            </div>
            <div class="list-side">
              <StatusTag :status="statusOf(check)" size="sm" />
              <span class="mono">{{ formatLatency(check.latency_ms) }}</span>
              <el-button
                size="small"
                :loading="testingId === check.id"
                :disabled="Boolean(testingId) && testingId !== check.id"
                @click="runTest(check)"
              >
                试运行
              </el-button>
            </div>
          </li>
        </ul>
      </div>
    </div>

    <el-dialog v-model="testOpen" :title="`试运行：${testTitle}`" width="640px">
      <div v-if="testResult" class="test-body">
        <div class="result-head">
          <StatusTag :status="testResult.status" size="sm" />
          <span class="muted">{{ formatLatency(testResult.latency_ms) }}</span>
        </div>
        <p class="result-label">Check 定义</p>
        <pre class="code-block">{{ pretty(testResult.check) }}</pre>
        <p class="result-label">Evidence</p>
        <pre class="code-block">{{ pretty(testResult.evidence) }}</pre>
      </div>
      <template #footer>
        <el-button @click="testOpen = false">关闭</el-button>
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
.alert-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--vigil-space-2);
}

.tag {
  margin-left: var(--vigil-space-1);
  padding: 0 4px;
  border: 1px solid var(--vigil-color-border);
  border-radius: var(--vigil-radius-sm);
  color: var(--vigil-color-text-muted);
  font-weight: 400;

  &--critical {
    border-color: var(--vigil-color-critical);
    color: var(--vigil-color-critical);
  }
}

.error-box {
  margin-bottom: var(--vigil-space-3);
}

.list-side {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: var(--vigil-space-1);
}

.test-body {
  display: flex;
  flex-direction: column;
  gap: var(--vigil-space-2);
}

.result-head {
  display: flex;
  align-items: center;
  gap: var(--vigil-space-2);
}

.result-label {
  margin: var(--vigil-space-2) 0 4px;
  font-size: var(--vigil-font-xs);
  color: var(--vigil-color-text-muted);
}

.code-block {
  margin: 0;
  padding: var(--vigil-space-2);
  max-height: 260px;
  overflow: auto;
  border-radius: var(--vigil-radius-sm);
  background: var(--vigil-color-bg-subtle);
  font-family: var(--vigil-font-mono);
  font-size: 11px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
