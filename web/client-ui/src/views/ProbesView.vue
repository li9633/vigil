<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  EmptyState,
  LiveBadge,
  LoadingBlock,
  RefreshFab,
  StatusTag,
  formatLatency,
  getErrorMessage,
  usePolling,
  type ProbeCatalogItem,
  type ProbeParamSpec,
  type ProbeTestResponse,
} from '@vigil/shared'
import { clientApi } from '@/api/client'

const probes = ref<ProbeCatalogItem[]>([])
const directory = ref('')
const loading = ref(false)
const installing = ref(false)
const error = ref('')
const fileInput = ref<HTMLInputElement | null>(null)

const testOpen = ref(false)
const testTarget = ref<ProbeCatalogItem | null>(null)
const testParams = reactive<Record<string, unknown>>({})
const testExpect = ref('')
const testRunning = ref(false)
const testResult = ref<ProbeTestResponse | null>(null)

const sorted = computed(() =>
  [...probes.value].sort((a, b) => {
    const localDiff = Number(b.source === 'local') - Number(a.source === 'local')
    if (localDiff !== 0) return localDiff
    return String(a.id).localeCompare(String(b.id))
  }),
)

const localCount = computed(
  () => probes.value.filter((item) => item.source === 'local').length,
)
const invalidCount = computed(
  () => probes.value.filter((item) => item.valid === false).length,
)

function fields(probe: ProbeCatalogItem): Array<[string, ProbeParamSpec]> {
  return Object.entries(probe.params_schema ?? {})
}

function paramNames(probe: ProbeCatalogItem): string {
  const names = fields(probe).map(([name]) => name)
  return names.length ? names.join(', ') : '—'
}

function shortSha(probe: ProbeCatalogItem): string {
  const value = String(probe.sha256 || '')
  return value ? `${value.slice(0, 12)}…` : '—'
}

function pretty(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2)
}

async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const payload = await clientApi.probes()
    probes.value = Array.isArray(payload.items) ? payload.items : []
    directory.value = payload.directory || ''
  } catch (err) {
    error.value = getErrorMessage(err, '探测器加载失败')
  } finally {
    loading.value = false
  }
}

function pickFile() {
  fileInput.value?.click()
}

async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || installing.value) return
  installing.value = true
  try {
    const payload = await clientApi.installProbe(file)
    probes.value = Array.isArray(payload.items) ? payload.items : []
    directory.value = payload.directory || ''
    ElMessage.success(`已安装 ${file.name}`)
  } catch (err) {
    ElMessage.error(getErrorMessage(err, '安装失败，请检查 probe 包'))
  } finally {
    installing.value = false
    input.value = ''
  }
}

async function remove(probe: ProbeCatalogItem) {
  try {
    await ElMessageBox.confirm(
      `删除本地探测器 ${probe.id}？引用它的检测项将变为未知状态。`,
      '删除探测器',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const payload = await clientApi.deleteProbe(probe.id)
    probes.value = Array.isArray(payload.items) ? payload.items : []
    directory.value = payload.directory || ''
    ElMessage.success('已删除')
  } catch (err) {
    ElMessage.error(getErrorMessage(err, '删除失败'))
  }
}

function defaultParam(spec: ProbeParamSpec): unknown {
  if (spec.default !== undefined && spec.default !== null) return spec.default
  if (spec.type === 'boolean') return false
  if (spec.type === 'number' || spec.type === 'integer') return 0
  if (spec.type === 'enum') return (spec.values ?? [''])[0] ?? ''
  return ''
}

function setParam(name: string, value: unknown) {
  testParams[name] = value
}

function openTest(probe: ProbeCatalogItem) {
  testTarget.value = probe
  Object.keys(testParams).forEach((key) => delete testParams[key])
  for (const [name, spec] of fields(probe)) {
    testParams[name] = defaultParam(spec)
  }
  testExpect.value = pretty(probe.default_expect ?? {})
  testResult.value = null
  testOpen.value = true
}

function parseExpect(): Record<string, unknown> | undefined {
  const text = testExpect.value.trim()
  if (!text) return undefined
  const parsed = JSON.parse(text)
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('判定条件必须是 JSON 对象')
  }
  return parsed as Record<string, unknown>
}

async function runTest() {
  const probe = testTarget.value
  if (!probe || testRunning.value) return
  let expect: Record<string, unknown> | undefined
  try {
    expect = parseExpect()
  } catch (err) {
    ElMessage.error(getErrorMessage(err, '判定条件必须是合法 JSON 对象'))
    return
  }
  testRunning.value = true
  try {
    testResult.value = await clientApi.testProbe(probe.id, { ...testParams }, expect)
  } catch (err) {
    ElMessage.error(getErrorMessage(err, '试运行失败'))
  } finally {
    testRunning.value = false
  }
}

const poll = usePolling(load, {
  interval: 15000,
  isError: () => Boolean(error.value),
})
</script>

<template>
  <div class="page pb-fab">
    <div class="page-head">
      <div>
        <h2 class="page-title">探测器</h2>
        <p class="page-sub">
          共 {{ probes.length }} 个 · 本地 {{ localCount }} 个
          <template v-if="invalidCount"> · 异常 {{ invalidCount }} 个</template>
        </p>
      </div>
      <div class="head-actions">
        <LiveBadge :at="poll.lastUpdated" :paused="poll.paused" />
        <el-button size="small" type="primary" :loading="installing" @click="pickFile">
          安装本地探测器
        </el-button>
      </div>
    </div>

    <input
      ref="fileInput"
      class="hidden-input"
      type="file"
      accept=".gz,.tgz,application/gzip"
      @change="upload"
    />

    <el-alert
      class="risk-alert"
      type="warning"
      show-icon
      :closable="false"
      title="本地探测器以管理员权限执行"
      description="仅安装可信来源的扩展包；安装、删除与试运行均写入本地审计日志。"
    />

    <el-alert v-if="error" class="error-box" type="error" show-icon :closable="false">
      <div class="alert-row">
        <span>{{ error }}</span>
        <el-button size="small" text type="primary" @click="poll.retry">重试</el-button>
      </div>
    </el-alert>

    <LoadingBlock v-if="loading && !probes.length" min-height="180px" text="正在读取探测器…" />

    <EmptyState
      v-else-if="!probes.length && !loading"
      title="暂无探测器"
      description="内置探测器随客户端提供，也可上传安装本地探测器扩展。"
    >
      <template #action>
        <el-button size="small" type="primary" @click="pickFile">安装本地探测器</el-button>
      </template>
    </EmptyState>

    <div v-else class="panel">
      <div class="panel-body panel-body--flush">
        <ul class="list">
          <li v-for="probe in sorted" :key="probe.id" class="list-item">
            <div class="list-main">
              <p class="list-title">
                {{ probe.name || probe.id }}
                <span class="mono tag">{{ probe.source === 'local' ? '本地' : '内置' }}</span>
                <span v-if="probe.remote_allowed === false" class="mono tag">仅本地</span>
                <span v-if="probe.valid === false" class="mono tag tag--critical">不可用</span>
              </p>
              <p class="list-meta mono">{{ probe.id }} · {{ probe.version || '—' }}</p>
              <p v-if="probe.description" class="list-meta">{{ probe.description }}</p>
              <p class="list-meta">
                参数 {{ paramNames(probe) }} · 默认超时 {{ probe.default_timeout ?? '—' }}s
                <template v-if="probe.source === 'local'">
                  · SHA {{ shortSha(probe) }}
                </template>
              </p>
              <p v-if="probe.error" class="list-meta text-critical">{{ probe.error }}</p>
            </div>
            <div class="list-side">
              <el-button
                size="small"
                :disabled="probe.valid === false"
                @click="openTest(probe)"
              >
                试运行
              </el-button>
              <el-button
                v-if="probe.source === 'local'"
                size="small"
                text
                type="danger"
                @click="remove(probe)"
              >
                删除
              </el-button>
            </div>
          </li>
        </ul>
      </div>
    </div>

    <el-dialog
      v-model="testOpen"
      :title="`试运行：${testTarget?.name || testTarget?.id || ''}`"
      width="640px"
    >
      <div v-if="testTarget" class="test-body">
        <p class="muted mono">
          {{ testTarget.id }} · {{ testTarget.source }} · {{ testTarget.version || '—' }}
        </p>
        <div class="test-grid">
          <label v-for="[name, spec] in fields(testTarget)" :key="name" class="field">
            <span>{{ spec.label || name }}<em v-if="spec.required"> *</em></span>
            <el-select
              v-if="spec.type === 'enum'"
              :model-value="testParams[name] as string | number"
              @update:model-value="(value: string | number) => setParam(name, value)"
            >
              <el-option
                v-for="item in spec.values || []"
                :key="String(item)"
                :label="String(item)"
                :value="item"
              />
            </el-select>
            <el-switch
              v-else-if="spec.type === 'boolean'"
              :model-value="Boolean(testParams[name])"
              @update:model-value="(value: boolean) => setParam(name, value)"
            />
            <el-input
              v-else
              :model-value="String(testParams[name] ?? '')"
              :placeholder="spec.placeholder || ''"
              @update:model-value="
                (value: string) =>
                  setParam(
                    name,
                    spec.type === 'number' || spec.type === 'integer' ? Number(value) : value,
                  )
              "
            />
          </label>
        </div>
        <label class="field field--wide">
          <span>判定条件（JSON，留空使用默认）</span>
          <textarea v-model="testExpect" class="code-area" rows="5" />
        </label>
        <div v-if="testResult" class="test-result">
          <div class="result-head">
            <StatusTag :status="testResult.status" size="sm" />
            <span class="muted">{{ formatLatency(testResult.latency_ms) }}</span>
          </div>
          <p class="result-label">观测结果</p>
          <pre class="code-block">{{ pretty(testResult.observation) }}</pre>
          <p class="result-label">判定证据</p>
          <pre class="code-block">{{ pretty(testResult.evidence) }}</pre>
        </div>
      </div>
      <template #footer>
        <el-button @click="testOpen = false">关闭</el-button>
        <el-button type="primary" :loading="testRunning" @click="runTest">运行</el-button>
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
.head-actions {
  display: flex;
  gap: var(--vigil-space-2);
}

.hidden-input {
  display: none;
}

.risk-alert,
.error-box {
  margin-bottom: var(--vigil-space-3);
}

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

.test-body {
  display: flex;
  flex-direction: column;
  gap: var(--vigil-space-3);
}

.test-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: var(--vigil-space-3);
}

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: var(--vigil-font-xs);
  color: var(--vigil-color-text-muted);

  &--wide {
    grid-column: 1 / -1;
  }

  em {
    color: var(--vigil-color-critical);
    font-style: normal;
  }
}

.code-area {
  width: 100%;
  padding: 6px 8px;
  border: 1px solid var(--vigil-color-border);
  border-radius: var(--vigil-radius-sm);
  background: var(--vigil-color-surface);
  color: var(--vigil-color-text);
  font-family: var(--vigil-font-mono);
  font-size: var(--vigil-font-xs);
  resize: vertical;
}

.test-result {
  border-top: 1px solid var(--vigil-color-border);
  padding-top: var(--vigil-space-3);
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
  max-height: 220px;
  overflow: auto;
  border-radius: var(--vigil-radius-sm);
  background: var(--vigil-color-bg-subtle);
  font-family: var(--vigil-font-mono);
  font-size: 11px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
