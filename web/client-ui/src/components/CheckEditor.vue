<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type {
  CheckDefinition,
  ProbeCatalogItem,
  ProbeParamSpec,
} from '@vigil/shared'

interface CheckDraft {
  id: string
  name: string
  probe: string
  params: Record<string, unknown>
  paramTexts: Record<string, string>
  expect: Record<string, unknown>
  timeout: number | null
  depends_on: string[]
  enabled: boolean
}

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    checks: CheckDefinition[]
    probes: ProbeCatalogItem[]
    busy?: boolean
  }>(),
  { busy: false },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  save: [checks: CheckDefinition[]]
}>()

const CHECK_ID_RE = /^[a-z0-9][a-z0-9._-]{0,63}$/
const EXPECT_KINDS = [
  'carrier',
  'pppoe',
  'route',
  'dns',
  'http',
  'tcp',
  'process',
  'icmp',
  'command',
  'json',
]
const EXPECT_FLAGS: Record<string, Array<[string, string]>> = {
  carrier: [['up', '链路 up']],
  pppoe: [
    ['up', '接口 up'],
    ['require_ip', '要求存在 IP'],
  ],
  route: [['has_route', '存在默认路由']],
  dns: [['resolved', '解析成功']],
  http: [],
  tcp: [['connected', '连接成功']],
  process: [['running', '进程 / 服务运行']],
  icmp: [['reachable', '可达']],
  command: [],
  json: [['exists', '字段存在']],
}
const EXPECT_STRINGS: Record<string, Array<[string, string]>> = {
  dns: [['contains', '包含地址']],
  http: [['body_contains', '正文包含']],
  route: [['egress_ip', '出口 IP']],
  command: [['stdout_contains', '输出包含']],
  json: [['path', 'JSON 路径']],
}
const EXPECT_NUMBERS: Record<string, Array<[string, string]>> = {
  http: [['max_latency_ms', '最大时延（ms）']],
  tcp: [['max_latency_ms', '最大时延（ms）']],
  icmp: [
    ['max_latency_ms', '最大时延（ms）'],
    ['max_loss', '最大丢包'],
  ],
}
const EXPECT_INT_LISTS: Record<string, Array<[string, string]>> = {
  http: [['status', '允许状态码']],
  command: [['exit_code', '允许退出码']],
}

const draft = ref<CheckDraft[]>([])
const selected = ref(0)
const error = ref('')

const current = computed<CheckDraft | null>(() => draft.value[selected.value] ?? null)

const probeMap = computed(() => new Map(props.probes.map((probe) => [probe.id, probe])))
const availableProbes = computed(() =>
  [...props.probes].sort((a, b) => a.id.localeCompare(b.id)),
)

function genSuffix(): string {
  return Math.random().toString(36).slice(2, 6)
}

function uniqueId(base: string): string {
  const taken = new Set(draft.value.map((item) => item.id))
  if (!taken.has(base)) return base
  for (let index = 2; index < 100; index += 1) {
    const candidate = `${base}-${index}`
    if (!taken.has(candidate)) return candidate
  }
  return `${base}-${genSuffix()}`
}

function textOf(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}

function specOf(check: CheckDraft): ProbeCatalogItem | null {
  return probeMap.value.get(check.probe) ?? null
}

function paramsSchemaOf(check: CheckDraft): Record<string, ProbeParamSpec> {
  return specOf(check)?.params_schema ?? {}
}

function defaultParams(probe: ProbeCatalogItem | null): {
  params: Record<string, unknown>
  texts: Record<string, string>
} {
  const params: Record<string, unknown> = {}
  const texts: Record<string, string> = {}
  for (const [name, spec] of Object.entries(probe?.params_schema ?? {})) {
    if (spec && Object.prototype.hasOwnProperty.call(spec, 'default')) {
      params[name] = spec.default
      if (spec.type === 'object' || spec.type === 'array') {
        texts[name] = JSON.stringify(spec.default, null, 2)
      }
    }
  }
  return { params, texts }
}

function toDraft(check: CheckDefinition): CheckDraft {
  const spec = probeMap.value.get(check.probe) ?? null
  const rawParams = { ...(check.params ?? {}) }
  const texts: Record<string, string> = {}
  for (const [name, paramSpec] of Object.entries(spec?.params_schema ?? {})) {
    if (paramSpec?.type === 'object' || paramSpec?.type === 'array') {
      texts[name] = textOf(rawParams[name] ?? paramSpec.default)
    }
  }
  const expect = { ...(check.expect ?? {}) }
  if (!expect.kind && spec?.default_expect?.kind) expect.kind = spec.default_expect.kind
  return {
    id: check.id,
    name: check.name || check.id,
    probe: check.probe,
    params: rawParams,
    paramTexts: texts,
    expect,
    timeout: check.timeout ?? null,
    depends_on: [...(check.depends_on ?? [])],
    enabled: check.enabled !== false,
  }
}

function reset(): void {
  draft.value = props.checks.map(toDraft)
  selected.value = 0
  error.value = ''
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) reset()
  },
)

function close(): void {
  emit('update:modelValue', false)
}

function addCheck(): void {
  const probe = availableProbes.value[0] ?? null
  const defaults = defaultParams(probe)
  const draftItem: CheckDraft = {
    id: uniqueId('new-check'),
    name: '新检测项',
    probe: probe?.id ?? '',
    params: defaults.params,
    paramTexts: defaults.texts,
    expect: { ...(probe?.default_expect ?? { kind: 'http' }) },
    timeout: probe?.default_timeout ?? null,
    depends_on: [],
    enabled: true,
  }
  draft.value.push(draftItem)
  selected.value = draft.value.length - 1
}

function removeCheck(index: number): void {
  draft.value.splice(index, 1)
  if (selected.value >= draft.value.length) selected.value = draft.value.length - 1
  if (selected.value < 0) selected.value = 0
}

function changeProbe(check: CheckDraft, probeId: string): void {
  check.probe = probeId
  const probe = probeMap.value.get(probeId) ?? null
  const defaults = defaultParams(probe)
  check.params = defaults.params
  check.paramTexts = defaults.texts
  check.expect = { ...(probe?.default_expect ?? { kind: 'http' }) }
  if (check.timeout === null && probe?.default_timeout) check.timeout = probe.default_timeout
}

function paramValue(check: CheckDraft, name: string): unknown {
  return check.params[name]
}

function setParam(check: CheckDraft, name: string, value: unknown): void {
  check.params = { ...check.params, [name]: value }
}

function setParamNumber(check: CheckDraft, name: string, raw: string): void {
  const next = { ...check.params }
  if (!raw.trim()) delete next[name]
  else next[name] = Number(raw)
  check.params = next
}

function setParamText(check: CheckDraft, name: string, value: string): void {
  check.paramTexts = { ...check.paramTexts, [name]: value }
}

function expectKind(check: CheckDraft): string {
  const kind = check.expect.kind
  return typeof kind === 'string' ? kind : ''
}

function setExpectKind(check: CheckDraft, kind: string): void {
  const probe = specOf(check)
  const defaults =
    probe?.default_expect && probe.default_expect.kind === kind
      ? { ...probe.default_expect }
      : { kind }
  check.expect = defaults
}

function expectFlag(check: CheckDraft, name: string): boolean {
  return check.expect[name] === true
}

function setExpectFlag(check: CheckDraft, name: string, value: boolean): void {
  const next = { ...check.expect }
  if (value) next[name] = true
  else delete next[name]
  check.expect = next
}

function expectString(check: CheckDraft, name: string): string {
  const value = check.expect[name]
  return typeof value === 'string' ? value : ''
}

function setExpectString(check: CheckDraft, name: string, value: string): void {
  const next = { ...check.expect }
  if (value) next[name] = value
  else delete next[name]
  check.expect = next
}

function expectNumber(check: CheckDraft, name: string): number | null {
  const value = check.expect[name]
  return typeof value === 'number' ? value : null
}

function setExpectNumberInput(check: CheckDraft, name: string, raw: string): void {
  const next = { ...check.expect }
  if (!raw.trim()) delete next[name]
  else next[name] = Number(raw)
  check.expect = next
}

function expectIntList(check: CheckDraft, name: string): string {
  const value = check.expect[name]
  if (Array.isArray(value)) return value.join(', ')
  if (typeof value === 'number') return String(value)
  return ''
}

function setExpectIntList(check: CheckDraft, name: string, raw: string): void {
  const next = { ...check.expect }
  const items = raw
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) => Number(item))
    .filter((item) => Number.isInteger(item))
  if (items.length) next[name] = items
  else delete next[name]
  check.expect = next
}

function expectJsonEquals(check: CheckDraft): string {
  const value = check.expect.equals
  if (value === undefined) return ''
  return typeof value === 'string' ? value : JSON.stringify(value)
}

function setExpectJsonEquals(check: CheckDraft, raw: string): void {
  const next = { ...check.expect }
  if (!raw.trim()) delete next.equals
  else {
    try {
      next.equals = JSON.parse(raw)
    } catch {
      next.equals = raw
    }
  }
  check.expect = next
}

function hasCycle(graph: Record<string, string[]>): boolean {
  const state: Record<string, number> = {}
  const visit = (node: string): boolean => {
    if (state[node] === 2) return false
    if (state[node] === 1) return true
    state[node] = 1
    for (const dep of graph[node] ?? []) {
      if (visit(dep)) return true
    }
    state[node] = 2
    return false
  }
  return Object.keys(graph).some((node) => visit(node))
}

function dependencyOptions(check: CheckDraft): CheckDraft[] {
  return draft.value.filter((item) => item.id !== check.id)
}

function toggleDepends(check: CheckDraft, id: string, checked: boolean): void {
  if (checked) {
    if (!check.depends_on.includes(id)) check.depends_on.push(id)
  } else {
    check.depends_on = check.depends_on.filter((item) => item !== id)
  }
}

function buildPayload(): CheckDefinition[] {
  const errors: string[] = []
  const seen = new Set<string>()

  const items = draft.value.map((check, index) => {
    const label = check.name || `第 ${index + 1} 项`
    const cid = check.id.trim()
    if (!CHECK_ID_RE.test(cid)) {
      errors.push(`${label}：ID 只能包含小写字母、数字、点、下划线、短横线，且以字母或数字开头`)
    }
    if (seen.has(cid)) errors.push(`${label}：ID 重复（${cid}）`)
    seen.add(cid)
    if (!check.name.trim()) errors.push(`${label}：名称不能为空`)

    const probe = probeMap.value.get(check.probe)
    if (!probe) errors.push(`${label}：探测器不存在（${check.probe}）`)

    const params: Record<string, unknown> = {}
    for (const [name, spec] of Object.entries(paramsSchemaOf(check))) {
      const value = paramValue(check, name)
      if (spec.type === 'object' || spec.type === 'array') {
        const text = (check.paramTexts[name] ?? '').trim()
        if (!text) {
          if (spec.required) errors.push(`${label}：参数 ${name} 不能为空`)
          continue
        }
        try {
          params[name] = JSON.parse(text)
        } catch {
          errors.push(`${label}：参数 ${name} 不是合法 JSON`)
        }
      } else if (value === '' || value === undefined || value === null) {
        if (spec.required) errors.push(`${label}：缺少必填参数 ${name}`)
      } else {
        params[name] = value
      }
    }

    const kind = expectKind(check)
    if (!kind) errors.push(`${label}：expect.kind 不能为空`)
    else if (!EXPECT_KINDS.includes(kind)) errors.push(`${label}：expect.kind 非法`)

    const timeout =
      check.timeout === null || check.timeout === undefined
        ? null
        : Number(check.timeout)
    if (timeout !== null) {
      if (!Number.isFinite(timeout) || timeout <= 0 || timeout > 60) {
        errors.push(`${label}：超时必须在 (0, 60] 秒内`)
      }
    }

    for (const dep of check.depends_on) {
      if (dep === cid) errors.push(`${label}：不能依赖自身`)
      if (!seen.has(dep) && !draft.value.some((item) => item.id.trim() === dep)) {
        errors.push(`${label}：依赖的检测项不存在（${dep}）`)
      }
    }

    return {
      id: cid,
      name: check.name.trim(),
      probe: check.probe,
      params,
      expect: { ...check.expect },
      timeout,
      depends_on: [...check.depends_on],
      enabled: check.enabled,
    }
  })

  if (
    hasCycle(
      Object.fromEntries(items.map((item) => [item.id, item.depends_on])),
    )
  ) {
    errors.push('检测项依赖存在环')
  }

  if (errors.length) {
    error.value = [...new Set(errors)].join('；')
    return []
  }
  error.value = ''
  return items
}

function submit(): void {
  if (props.busy) return
  const payload = buildPayload()
  if (!payload.length && draft.value.length) return
  emit('save', payload)
}
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue" class="check-mask" @click.self="close">
      <div class="check-dialog" role="dialog" aria-modal="true" aria-label="编辑检测项">
        <header class="check-dialog__head">
          <div>
            <h3 class="check-dialog__title">编辑检测项</h3>
            <p class="check-dialog__hint">
              仅可引用客户端已安装的探测器；保存后本地立即生效。
            </p>
          </div>
          <button class="icon-btn" type="button" aria-label="关闭" @click="close">×</button>
        </header>

        <div class="check-dialog__body">
          <aside class="check-nav">
            <button
              v-for="(check, index) in draft"
              :key="check.id"
              type="button"
              class="check-nav__item"
              :class="{ active: index === selected }"
              @click="selected = index"
            >
              <span class="check-nav__name">{{ check.name || check.id || '未命名' }}</span>
              <span class="check-nav__meta mono">{{ check.probe || '—' }}</span>
            </button>
            <button type="button" class="check-nav__add" @click="addCheck">
              + 新增检测项
            </button>
          </aside>

          <section v-if="current" class="check-form">
            <div class="form-section">
              <div class="form-section__head">
                <span class="form-section__title">基本信息</span>
                <button
                  v-if="draft.length > 1"
                  type="button"
                  class="text-danger"
                  @click="removeCheck(selected)"
                >
                  删除此检测项
                </button>
              </div>
              <div class="form-grid">
                <label class="field">
                  <span>ID</span>
                  <input
                    v-model="current.id"
                    class="g-input mono"
                    type="text"
                    placeholder="wan-link"
                  />
                </label>
                <label class="field">
                  <span>名称</span>
                  <input v-model="current.name" class="g-input" type="text" />
                </label>
                <label class="field">
                  <span>探测器</span>
                  <select
                    class="g-input"
                    :value="current.probe"
                    @change="changeProbe(current, ($event.target as HTMLSelectElement).value)"
                  >
                    <option
                      v-for="probe in availableProbes"
                      :key="probe.id"
                      :value="probe.id"
                    >
                      {{ probe.id }} · {{ probe.name || '' }}
                    </option>
                    <option
                      v-if="current.probe && !availableProbes.some((probe) => probe.id === current.probe)"
                      :value="current.probe"
                    >
                      {{ current.probe }}（缺失）
                    </option>
                  </select>
                </label>
                <label class="field">
                  <span>超时（秒）</span>
                  <input
                    v-model.number="current.timeout"
                    class="g-input"
                    type="number"
                    min="0.1"
                    max="60"
                    step="0.5"
                  />
                </label>
                <label class="field field--check">
                  <input v-model="current.enabled" type="checkbox" />
                  <span>启用该检测项</span>
                </label>
              </div>
            </div>

            <div class="form-section">
              <span class="form-section__title">参数</span>
              <div
                v-if="Object.keys(paramsSchemaOf(current)).length"
                class="form-grid form-grid--tight"
              >
                <label
                  v-for="(spec, name) in paramsSchemaOf(current)"
                  :key="name"
                  class="field"
                  :class="{ 'field--wide': spec.type === 'object' || spec.type === 'array' }"
                >
                  <span>
                    {{ spec.label || name }}
                    <span v-if="spec.required" class="required">*</span>
                  </span>
                  <select
                    v-if="spec.type === 'enum'"
                    class="g-input"
                    :value="paramValue(current, String(name))"
                    @change="setParam(current, String(name), ($event.target as HTMLSelectElement).value)"
                  >
                    <option value="">请选择</option>
                    <option v-for="item in spec.values ?? []" :key="String(item)" :value="item">
                      {{ item }}
                    </option>
                  </select>
                  <label v-else-if="spec.type === 'boolean'" class="check-inline">
                    <input
                      type="checkbox"
                      :checked="paramValue(current, String(name)) === true"
                      @change="setParam(current, String(name), ($event.target as HTMLInputElement).checked)"
                    />
                    <span>{{ spec.placeholder || '启用' }}</span>
                  </label>
                  <textarea
                    v-else-if="spec.type === 'object' || spec.type === 'array'"
                    class="g-input mono code-area"
                    rows="3"
                    :value="current.paramTexts[String(name)] ?? ''"
                    :placeholder="spec.placeholder || 'JSON'"
                    @input="setParamText(current, String(name), ($event.target as HTMLTextAreaElement).value)"
                  />
                  <input
                    v-else-if="spec.type === 'integer' || spec.type === 'number'"
                    class="g-input"
                    type="number"
                    :value="paramValue(current, String(name)) ?? ''"
                    @input="setParamNumber(current, String(name), ($event.target as HTMLInputElement).value)"
                  />
                  <input
                    v-else
                    class="g-input"
                    type="text"
                    :value="paramValue(current, String(name)) ?? ''"
                    :placeholder="spec.placeholder || ''"
                    @input="setParam(current, String(name), ($event.target as HTMLInputElement).value)"
                  />
                </label>
              </div>
              <p v-else class="muted">该探测器无需参数。</p>
            </div>

            <div class="form-section">
              <span class="form-section__title">期望判定</span>
              <div class="form-grid form-grid--tight">
                <label class="field">
                  <span>kind</span>
                  <select
                    class="g-input"
                    :value="expectKind(current)"
                    @change="setExpectKind(current, ($event.target as HTMLSelectElement).value)"
                  >
                    <option value="">请选择</option>
                    <option v-for="kind in EXPECT_KINDS" :key="kind" :value="kind">
                      {{ kind }}
                    </option>
                  </select>
                </label>
                <label
                  v-for="[name, label] in EXPECT_STRINGS[expectKind(current)] ?? []"
                  :key="name"
                  class="field"
                >
                  <span>{{ label }}</span>
                  <input
                    class="g-input"
                    type="text"
                    :value="expectString(current, name)"
                    @input="setExpectString(current, name, ($event.target as HTMLInputElement).value)"
                  />
                </label>
                <label
                  v-for="[name, label] in EXPECT_NUMBERS[expectKind(current)] ?? []"
                  :key="name"
                  class="field"
                >
                  <span>{{ label }}</span>
                  <input
                    class="g-input"
                    type="number"
                    min="0"
                    :value="expectNumber(current, name) ?? ''"
                    @input="setExpectNumberInput(current, name, ($event.target as HTMLInputElement).value)"
                  />
                </label>
                <label
                  v-for="[name, label] in EXPECT_INT_LISTS[expectKind(current)] ?? []"
                  :key="name"
                  class="field"
                >
                  <span>{{ label }}（逗号分隔）</span>
                  <input
                    class="g-input"
                    type="text"
                    :value="expectIntList(current, name)"
                    placeholder="200, 204"
                    @input="setExpectIntList(current, name, ($event.target as HTMLInputElement).value)"
                  />
                </label>
                <label
                  v-for="[name, label] in EXPECT_FLAGS[expectKind(current)] ?? []"
                  :key="name"
                  class="field field--check"
                >
                  <input
                    type="checkbox"
                    :checked="expectFlag(current, name)"
                    @change="setExpectFlag(current, name, ($event.target as HTMLInputElement).checked)"
                  />
                  <span>{{ label }}</span>
                </label>
                <label v-if="expectKind(current) === 'json'" class="field field--wide">
                  <span>equals（JSON 或字符串）</span>
                  <input
                    class="g-input mono"
                    type="text"
                    :value="expectJsonEquals(current)"
                    @input="setExpectJsonEquals(current, ($event.target as HTMLInputElement).value)"
                  />
                </label>
              </div>
            </div>

            <div class="form-section">
              <div class="form-section__head">
                <span class="form-section__title">依赖</span>
                <span class="muted">被 Guard 引用时按依赖闭包执行</span>
              </div>
              <div v-if="dependencyOptions(current).length" class="dep-picker">
                <label
                  v-for="item in dependencyOptions(current)"
                  :key="item.id"
                  class="dep-option"
                >
                  <input
                    type="checkbox"
                    :checked="current.depends_on.includes(item.id)"
                    @change="toggleDepends(current, item.id, ($event.target as HTMLInputElement).checked)"
                  />
                  <span class="mono">{{ item.id }}</span>
                  <span class="muted">{{ item.name }}</span>
                </label>
              </div>
              <p v-else class="muted">暂无其他检测项可依赖。</p>
            </div>
          </section>

          <section v-else class="check-form check-form--empty">
            <p class="muted">暂无检测项，点击左侧“新增检测项”开始配置。</p>
          </section>
        </div>

        <footer class="check-dialog__foot">
          <span class="form-error">{{ error }}</span>
          <div class="foot-actions">
            <button type="button" class="g-btn" @click="close">取消</button>
            <button
              type="button"
              class="g-btn g-btn--primary"
              :disabled="busy"
              @click="submit"
            >
              {{ busy ? '保存中…' : '保存' }}
            </button>
          </div>
        </footer>
      </div>
    </div>
  </Teleport>
</template>

<style scoped lang="scss">
.check-mask {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--vigil-space-4);
  background: var(--vigil-color-overlay);
}

.check-dialog {
  display: flex;
  flex-direction: column;
  width: min(1040px, 100%);
  max-height: 90vh;
  background: var(--vigil-color-surface);
  border-radius: var(--vigil-radius-md);
  box-shadow: var(--vigil-shadow-lg);
  overflow: hidden;
}

.check-dialog__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--vigil-space-3);
  padding: var(--vigil-space-4);
  border-bottom: 1px solid var(--vigil-color-border);
}

.check-dialog__title {
  margin: 0;
  font-size: var(--vigil-font-md);
}

.check-dialog__hint {
  margin: 4px 0 0;
  font-size: var(--vigil-font-xs);
  color: var(--vigil-color-text-muted);
}

.icon-btn {
  border: none;
  background: transparent;
  font-size: 22px;
  line-height: 1;
  color: var(--vigil-color-text-muted);
  cursor: pointer;
}

.check-dialog__body {
  display: flex;
  min-height: 0;
  flex: 1;
}

.check-nav {
  flex: 0 0 200px;
  padding: var(--vigil-space-3);
  border-right: 1px solid var(--vigil-color-border);
  overflow-y: auto;
  background: var(--vigil-color-bg-subtle);
}

.check-nav__item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  padding: 8px 10px;
  margin-bottom: 4px;
  border: 1px solid transparent;
  border-radius: var(--vigil-radius-sm);
  background: transparent;
  text-align: left;
  cursor: pointer;

  &.active {
    background: var(--vigil-color-surface);
    border-color: var(--vigil-color-border);
  }
}

.check-nav__name {
  font-size: var(--vigil-font-xs);
  font-weight: 600;
  color: var(--vigil-color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.check-nav__meta {
  font-size: 11px;
  color: var(--vigil-color-text-muted);
}

.check-nav__add {
  width: 100%;
  margin-top: var(--vigil-space-2);
  padding: 8px;
  border: 1px dashed var(--vigil-color-border);
  border-radius: var(--vigil-radius-sm);
  background: transparent;
  color: var(--vigil-color-primary);
  font-size: var(--vigil-font-xs);
  cursor: pointer;
}

.check-form {
  flex: 1;
  min-width: 0;
  padding: var(--vigil-space-4);
  overflow-y: auto;
}

.check-form--empty {
  display: flex;
  align-items: center;
  justify-content: center;
}

.form-section {
  margin-bottom: var(--vigil-space-4);

  &:last-child {
    margin-bottom: 0;
  }
}

.form-section__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--vigil-space-2);
  margin-bottom: var(--vigil-space-2);
}

.form-section__title {
  font-size: var(--vigil-font-xs);
  font-weight: 600;
  color: var(--vigil-color-text-secondary);
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--vigil-space-3);
}

.form-grid--tight {
  gap: var(--vigil-space-2);
}

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  font-size: var(--vigil-font-xs);
  color: var(--vigil-color-text-muted);

  &--wide {
    grid-column: 1 / -1;
  }

  &--check {
    flex-direction: row;
    align-items: center;
    gap: 6px;
    color: var(--vigil-color-text-secondary);
  }
}

.required {
  color: var(--vigil-color-critical);
}

.check-inline {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--vigil-font-xs);
}

.g-input {
  width: 100%;
  padding: 6px 8px;
  border: 1px solid var(--vigil-color-border);
  border-radius: var(--vigil-radius-sm);
  background: var(--vigil-color-surface);
  color: var(--vigil-color-text);
  font-size: var(--vigil-font-xs);
  font-family: inherit;
}

.code-area {
  resize: vertical;
  min-height: 64px;
}

.dep-picker {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 6px;
  padding: var(--vigil-space-2);
  border: 1px solid var(--vigil-color-border);
  border-radius: var(--vigil-radius-sm);
  background: var(--vigil-color-bg-subtle);
}

.dep-option {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  font-size: var(--vigil-font-xs);
}

.text-danger {
  border: none;
  background: transparent;
  color: var(--vigil-color-critical);
  font-size: 11px;
  cursor: pointer;
}

.check-dialog__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--vigil-space-3);
  padding: var(--vigil-space-3) var(--vigil-space-4);
  border-top: 1px solid var(--vigil-color-border);
}

.form-error {
  flex: 1;
  min-width: 0;
  font-size: 11px;
  color: var(--vigil-color-critical);
}

.foot-actions {
  display: flex;
  gap: var(--vigil-space-2);
}

.g-btn {
  padding: 6px 14px;
  border: 1px solid var(--vigil-color-border);
  border-radius: var(--vigil-radius-sm);
  background: var(--vigil-color-surface);
  color: var(--vigil-color-text);
  font-size: var(--vigil-font-xs);
  cursor: pointer;

  &--primary {
    border-color: var(--vigil-color-primary-solid);
    background: var(--vigil-color-primary-solid);
    color: var(--vigil-color-on-primary);
  }
}

.mono {
  font-family: var(--vigil-font-mono);
}

.muted {
  color: var(--vigil-color-text-muted);
  font-size: var(--vigil-font-xs);
}

@media (max-width: 720px) {
  .check-dialog {
    max-height: 96vh;
  }

  .check-dialog__body {
    flex-direction: column;
  }

  .check-nav {
    flex: 0 0 auto;
    max-height: 150px;
    border-right: none;
    border-bottom: 1px solid var(--vigil-color-border);
  }
}
</style>
