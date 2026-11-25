<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type {
  GuardCheckConfig,
  GuardDefinition,
  GuardPolicy,
  GuardRecoveryStep,
} from '../types'

interface CheckDraft extends GuardCheckConfig {
  paramsText: string
}

interface GuardDraft extends Omit<GuardDefinition, 'checks' | 'policy' | 'recovery'> {
  checks: CheckDraft[]
  policy: Required<GuardPolicy>
  recovery: GuardRecoveryStep[]
}

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    guards: GuardDefinition[]
    readonly?: boolean
    busy?: boolean
    title?: string
    hint?: string
    selectedId?: string
  }>(),
  { readonly: false, busy: false, title: '编辑守护项', hint: '', selectedId: '' },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  save: [guards: GuardDefinition[]]
}>()

const PROBES = ['carrier', 'pppoe', 'route', 'dns', 'http', 'process', 'port', 'script']
const ACTIONS = ['ifup_wan', 'restart_service']
const MODES = [
  { value: 'dependency_tree', label: '依赖树（默认）' },
  { value: 'all', label: '全部失败' },
  { value: 'any', label: '任一失败' },
  { value: 'quorum', label: '失败数阈值' },
]

const DEFAULT_POLICY: Required<GuardPolicy> = {
  interval: 30,
  fail_threshold: 2,
  fail_mode: 'consecutive',
  fail_window: 90,
  success_threshold: 1,
  cooldown: 300,
  max_actions_per_hour: 3,
  gave_up_after: 3,
  backoff: 'exponential',
}

const draft = ref<GuardDraft[]>([])
const selected = ref(0)
const error = ref('')

const current = computed<GuardDraft | null>(() => draft.value[selected.value] ?? null)

function genId(): string {
  const uuid = globalThis.crypto?.randomUUID?.()
  if (uuid) return uuid
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
    const rand = (Math.random() * 16) | 0
    const value = char === 'x' ? rand : (rand & 0x3) | 0x8
    return value.toString(16)
  })
}

function toDraft(guard: GuardDefinition): GuardDraft {
  const policy = { ...DEFAULT_POLICY, ...(guard.policy ?? {}) }
  return {
    id: guard.id || genId(),
    name: guard.name || '未命名守护项',
    template: guard.template ?? '',
    enabled: guard.enabled !== false,
    limits: { ...(guard.limits ?? {}) },
    evaluate: { ...(guard.evaluate ?? {}) },
    policy,
    recovery: (guard.recovery ?? []).map((step) => ({ ...step })),
    checks: (guard.checks ?? []).map((check) => ({
      ...check,
      id: check.id ?? '',
      probe: check.probe || 'carrier',
      params: { ...(check.params ?? {}) },
      paramsText: JSON.stringify(check.params ?? {}, null, 2),
      depends_on: [...(check.depends_on ?? [])],
      timeout: check.timeout ?? null,
      role: check.role ?? '',
    })),
  }
}

function reset(): void {
  draft.value = props.guards.map(toDraft)
  const index = props.selectedId
    ? draft.value.findIndex((guard) => guard.id === props.selectedId)
    : 0
  selected.value = index >= 0 ? index : 0
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

function addGuard(): void {
  draft.value.push(
    toDraft({
      id: genId(),
      name: '新守护项',
      template: '',
      enabled: true,
      checks: [{ probe: 'http', params: { url: ['http://connect.rom.miui.com/generate_204'] } }],
      policy: { ...DEFAULT_POLICY },
      recovery: [],
      limits: { max_risk_level: 'L1' },
      evaluate: { mode: 'dependency_tree' },
    }),
  )
  selected.value = draft.value.length - 1
}

function removeGuard(index: number): void {
  draft.value.splice(index, 1)
  if (selected.value >= draft.value.length) selected.value = draft.value.length - 1
  if (selected.value < 0) selected.value = 0
}

function addCheck(): void {
  if (!current.value) return
  current.value.checks.push({
    id: '',
    probe: 'http',
    params: {},
    paramsText: '{}',
    depends_on: [],
    timeout: null,
    role: '',
  })
}

function removeCheck(index: number): void {
  current.value?.checks.splice(index, 1)
}

function addRecovery(): void {
  if (!current.value) return
  current.value.recovery.push({
    action: 'ifup_wan',
    target: '',
    verify_after: 20,
    continue_on_failure: false,
  })
}

function removeRecovery(index: number): void {
  current.value?.recovery.splice(index, 1)
}

function dependsText(check: CheckDraft): string {
  return (check.depends_on ?? []).join(',')
}

function setDepends(check: CheckDraft, raw: string): void {
  check.depends_on = raw
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

function modeOf(guard: GuardDraft): string {
  const value = guard.evaluate?.mode
  return typeof value === 'string' && value ? value : 'dependency_tree'
}

function setMode(guard: GuardDraft, value: string): void {
  guard.evaluate = { ...(guard.evaluate ?? {}), mode: value }
}

function quorumOf(guard: GuardDraft): number {
  const value = guard.evaluate?.quorum
  return typeof value === 'number' ? value : 1
}

function setQuorum(guard: GuardDraft, value: number): void {
  guard.evaluate = { ...(guard.evaluate ?? {}), quorum: value }
}

function toNumber(value: unknown, fallback: number): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function buildPayload(): GuardDefinition[] {
  const errors: string[] = []
  const items = draft.value.map((guard, index) => {
    const label = guard.name || `第 ${index + 1} 项`
    if (!guard.name.trim()) errors.push(`${label}：名称不能为空`)
    if (!guard.checks.length) errors.push(`${label}：至少需要一个检测条件`)

    const checks = guard.checks.map((check, checkIndex) => {
      let params: Record<string, unknown> = {}
      const text = (check.paramsText ?? '').trim()
      if (text) {
        try {
          const parsed = JSON.parse(text)
          if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
            params = parsed as Record<string, unknown>
          } else {
            errors.push(`${label}：第 ${checkIndex + 1} 个检测参数的 JSON 必须是对象`)
          }
        } catch {
          errors.push(`${label}：第 ${checkIndex + 1} 个检测参数不是合法 JSON`)
        }
      }
      const item: GuardCheckConfig = {
        probe: check.probe,
        params,
      }
      if (check.id?.trim()) item.id = check.id.trim()
      if (check.depends_on?.length) item.depends_on = check.depends_on
      const timeout = Number(check.timeout)
      if (check.timeout !== null && check.timeout !== undefined && Number.isFinite(timeout) && timeout > 0) {
        item.timeout = timeout
      }
      if (check.role?.trim()) item.role = check.role.trim()
      return item
    })

    const recovery = guard.recovery
      .filter((step) => step.action)
      .map((step) => {
        const item: GuardRecoveryStep = { action: step.action }
        if (step.target?.trim()) item.target = step.target.trim()
        const verifyAfter = Number(step.verify_after)
        if (step.verify_after !== null && step.verify_after !== undefined && Number.isFinite(verifyAfter)) {
          item.verify_after = verifyAfter
        }
        if (step.continue_on_failure) item.continue_on_failure = true
        return item
      })

    const policy: Required<GuardPolicy> = {
      ...guard.policy,
      interval: toNumber(guard.policy.interval, DEFAULT_POLICY.interval),
      fail_threshold: toNumber(guard.policy.fail_threshold, DEFAULT_POLICY.fail_threshold),
      fail_mode: guard.policy.fail_mode === 'window' ? 'window' : 'consecutive',
      fail_window: toNumber(guard.policy.fail_window, DEFAULT_POLICY.fail_window),
      success_threshold: toNumber(guard.policy.success_threshold, DEFAULT_POLICY.success_threshold),
      cooldown: toNumber(guard.policy.cooldown, DEFAULT_POLICY.cooldown),
      max_actions_per_hour: toNumber(
        guard.policy.max_actions_per_hour,
        DEFAULT_POLICY.max_actions_per_hour,
      ),
      gave_up_after: toNumber(guard.policy.gave_up_after, DEFAULT_POLICY.gave_up_after),
      backoff: guard.policy.backoff || DEFAULT_POLICY.backoff,
    }

    return {
      id: guard.id || genId(),
      name: guard.name.trim(),
      template: guard.template ?? '',
      enabled: guard.enabled !== false,
      checks,
      policy,
      recovery,
      limits: { ...(guard.limits ?? {}) },
      evaluate: { ...(guard.evaluate ?? {}) },
    }
  })

  if (errors.length) {
    error.value = errors.join('；')
    return []
  }
  error.value = ''
  return items
}

function submit(): void {
  if (props.readonly || props.busy) return
  const payload = buildPayload()
  if (!payload.length) return
  emit('save', payload)
}
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue" class="guard-mask" @click.self="close">
      <div class="guard-dialog" role="dialog" aria-modal="true" :aria-label="title">
        <header class="guard-dialog__head">
          <div>
            <h3 class="guard-dialog__title">{{ title }}</h3>
            <p v-if="hint" class="guard-dialog__hint">{{ hint }}</p>
          </div>
          <button class="icon-btn" type="button" aria-label="关闭" @click="close">×</button>
        </header>

        <div class="guard-dialog__body">
          <aside class="guard-nav">
            <button
              v-for="(guard, index) in draft"
              :key="guard.id"
              type="button"
              class="guard-nav__item"
              :class="{ active: index === selected }"
              @click="selected = index"
            >
              <span class="guard-nav__name">{{ guard.name || '未命名' }}</span>
              <span class="guard-nav__meta">
                {{ guard.enabled === false ? '已停用' : '启用' }} · {{ guard.checks.length }} 项
              </span>
            </button>
            <button v-if="!readonly" type="button" class="guard-nav__add" @click="addGuard">
              + 新增守护项
            </button>
          </aside>

          <section v-if="current" class="guard-form">
            <div class="form-section">
              <div class="form-section__head">
                <span class="form-section__title">基本信息</span>
                <button
                  v-if="!readonly && draft.length > 1"
                  type="button"
                  class="text-danger"
                  @click="removeGuard(selected)"
                >
                  删除此守护项
                </button>
              </div>
              <div class="form-grid">
                <label class="field field--wide">
                  <span>名称</span>
                  <input v-model="current.name" class="g-input" type="text" :disabled="readonly" />
                </label>
                <label class="field">
                  <span>模板</span>
                  <input
                    v-model="current.template"
                    class="g-input"
                    type="text"
                    placeholder="内置模板或留空"
                    :disabled="readonly"
                  />
                </label>
                <label class="field field--check">
                  <input v-model="current.enabled" type="checkbox" :disabled="readonly" />
                  <span>启用该守护项</span>
                </label>
                <div class="field field--wide">
                  <span>ID</span>
                  <code class="mono id-text">{{ current.id }}</code>
                </div>
              </div>
            </div>

            <div class="form-section">
              <div class="form-section__head">
                <span class="form-section__title">检测条件</span>
                <button v-if="!readonly" type="button" class="text-btn" @click="addCheck">
                  + 添加检测
                </button>
              </div>
              <div class="form-grid">
                <label class="field">
                  <span>组合方式</span>
                  <select
                    class="g-input"
                    :value="modeOf(current)"
                    :disabled="readonly"
                    @change="setMode(current, ($event.target as HTMLSelectElement).value)"
                  >
                    <option v-for="mode in MODES" :key="mode.value" :value="mode.value">
                      {{ mode.label }}
                    </option>
                  </select>
                </label>
                <label v-if="modeOf(current) === 'quorum'" class="field">
                  <span>失败数阈值</span>
                  <input
                    class="g-input"
                    type="number"
                    min="1"
                    :value="quorumOf(current)"
                    :disabled="readonly"
                    @input="setQuorum(current, Number(($event.target as HTMLInputElement).value))"
                  />
                </label>
              </div>

              <div class="check-list">
                <div v-for="(check, index) in current.checks" :key="index" class="check-card">
                  <div class="check-card__head">
                    <span class="mono">检测 {{ index + 1 }}</span>
                    <button
                      v-if="!readonly"
                      type="button"
                      class="text-danger"
                      @click="removeCheck(index)"
                    >
                      移除
                    </button>
                  </div>
                  <div class="form-grid form-grid--tight">
                    <label class="field">
                      <span>探测器</span>
                      <select v-model="check.probe" class="g-input" :disabled="readonly">
                        <option v-for="probe in PROBES" :key="probe" :value="probe">{{ probe }}</option>
                      </select>
                    </label>
                    <label class="field">
                      <span>Check ID</span>
                      <input
                        v-model="check.id"
                        class="g-input mono"
                        type="text"
                        placeholder="默认同探测器名"
                        :disabled="readonly"
                      />
                    </label>
                    <label class="field">
                      <span>超时（秒）</span>
                      <input
                        v-model.number="check.timeout"
                        class="g-input"
                        type="number"
                        min="1"
                        placeholder="继承全局"
                        :disabled="readonly"
                      />
                    </label>
                    <label class="field">
                      <span>依赖（逗号分隔）</span>
                      <input
                        class="g-input"
                        type="text"
                        :value="dependsText(check)"
                        :disabled="readonly"
                        @input="setDepends(check, ($event.target as HTMLInputElement).value)"
                      />
                    </label>
                    <label class="field field--wide">
                      <span>参数 JSON</span>
                      <textarea
                        v-model="check.paramsText"
                        class="g-input mono code-area"
                        rows="3"
                        :disabled="readonly"
                      />
                    </label>
                  </div>
                </div>
                <p v-if="!current.checks.length" class="muted">尚未配置检测条件。</p>
              </div>
            </div>

            <div class="form-section">
              <span class="form-section__title">触发条件</span>
              <div class="form-grid form-grid--tight">
                <label class="field">
                  <span>检测间隔（秒）</span>
                  <input v-model.number="current.policy.interval" class="g-input" type="number" min="5" :disabled="readonly" />
                </label>
                <label class="field">
                  <span>连续失败阈值</span>
                  <input v-model.number="current.policy.fail_threshold" class="g-input" type="number" min="1" :disabled="readonly" />
                </label>
                <label class="field">
                  <span>失败统计方式</span>
                  <select v-model="current.policy.fail_mode" class="g-input" :disabled="readonly">
                    <option value="consecutive">连续失败</option>
                    <option value="window">窗口内失败</option>
                  </select>
                </label>
                <label class="field">
                  <span>窗口时长（秒）</span>
                  <input v-model.number="current.policy.fail_window" class="g-input" type="number" min="10" :disabled="readonly" />
                </label>
                <label class="field">
                  <span>恢复成功阈值</span>
                  <input v-model.number="current.policy.success_threshold" class="g-input" type="number" min="1" :disabled="readonly" />
                </label>
                <label class="field">
                  <span>动作冷却（秒）</span>
                  <input v-model.number="current.policy.cooldown" class="g-input" type="number" min="0" :disabled="readonly" />
                </label>
                <label class="field">
                  <span>每小时动作上限</span>
                  <input v-model.number="current.policy.max_actions_per_hour" class="g-input" type="number" min="1" :disabled="readonly" />
                </label>
                <label class="field">
                  <span>放弃前轮次</span>
                  <input v-model.number="current.policy.gave_up_after" class="g-input" type="number" min="1" :disabled="readonly" />
                </label>
              </div>
            </div>

            <div class="form-section">
              <div class="form-section__head">
                <span class="form-section__title">恢复动作</span>
                <button v-if="!readonly" type="button" class="text-btn" @click="addRecovery">
                  + 添加动作
                </button>
              </div>
              <div v-if="current.recovery.length" class="recovery-list">
                <div v-for="(step, index) in current.recovery" :key="index" class="recovery-row">
                  <span class="mono recovery-index">{{ index + 1 }}</span>
                  <select v-model="step.action" class="g-input" :disabled="readonly">
                    <option v-for="action in ACTIONS" :key="action" :value="action">{{ action }}</option>
                  </select>
                  <input
                    v-model="step.target"
                    class="g-input"
                    type="text"
                    placeholder="目标服务（restart_service 必填）"
                    :disabled="readonly || step.action !== 'restart_service'"
                  />
                  <input
                    v-model.number="step.verify_after"
                    class="g-input g-input--sm"
                    type="number"
                    min="0"
                    title="动作后等待验证的秒数"
                    :disabled="readonly"
                  />
                  <label class="field--check">
                    <input v-model="step.continue_on_failure" type="checkbox" :disabled="readonly" />
                    <span>失败继续</span>
                  </label>
                  <button v-if="!readonly" type="button" class="text-danger" @click="removeRecovery(index)">
                    移除
                  </button>
                </div>
              </div>
              <p v-else class="muted">未配置恢复动作，仅检测与告警。</p>
            </div>

            <div class="form-section">
              <span class="form-section__title">限制</span>
              <div class="form-grid">
                <label class="field">
                  <span>最高风险级别</span>
                  <select
                    class="g-input"
                    :value="String(current.limits?.max_risk_level ?? 'L1')"
                    :disabled="readonly"
                    @change="current.limits = { ...(current.limits ?? {}), max_risk_level: ($event.target as HTMLSelectElement).value }"
                  >
                    <option value="L1">L1（允许软恢复）</option>
                    <option value="L0">L0（只读，不执行动作）</option>
                  </select>
                </label>
              </div>
            </div>
          </section>

          <section v-else class="guard-form guard-form--empty">
            <p class="muted">暂无守护项，点击左侧“新增守护项”开始配置。</p>
          </section>
        </div>

        <footer class="guard-dialog__foot">
          <span class="form-error">{{ error }}</span>
          <div class="foot-actions">
            <button type="button" class="g-btn" @click="close">取消</button>
            <button
              v-if="!readonly"
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
.guard-mask {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--vigil-space-4);
  background: var(--vigil-color-overlay);
}

.guard-dialog {
  display: flex;
  flex-direction: column;
  width: min(1040px, 100%);
  max-height: 90vh;
  background: var(--vigil-color-surface);
  border-radius: var(--vigil-radius-md);
  box-shadow: var(--vigil-shadow-lg);
  overflow: hidden;
}

.guard-dialog__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--vigil-space-3);
  padding: var(--vigil-space-4);
  border-bottom: 1px solid var(--vigil-color-border);
}

.guard-dialog__title {
  margin: 0;
  font-size: var(--vigil-font-md);
}

.guard-dialog__hint {
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

.guard-dialog__body {
  display: flex;
  min-height: 0;
  flex: 1;
}

.guard-nav {
  flex: 0 0 190px;
  padding: var(--vigil-space-3);
  border-right: 1px solid var(--vigil-color-border);
  overflow-y: auto;
  background: var(--vigil-color-bg-subtle);
}

.guard-nav__item {
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

.guard-nav__name {
  font-size: var(--vigil-font-xs);
  font-weight: 600;
  color: var(--vigil-color-text);
}

.guard-nav__meta {
  font-size: 11px;
  color: var(--vigil-color-text-muted);
}

.guard-nav__add {
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

.guard-form {
  flex: 1;
  min-width: 0;
  padding: var(--vigil-space-4);
  overflow-y: auto;
}

.guard-form--empty {
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
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
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
    font-size: var(--vigil-font-xs);
    color: var(--vigil-color-text-secondary);
  }
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

  &:disabled {
    background: var(--vigil-color-bg-subtle);
    color: var(--vigil-color-text-muted);
  }

  &--sm {
    max-width: 92px;
  }
}

.code-area {
  resize: vertical;
  min-height: 64px;
}

.id-text {
  font-size: 11px;
  color: var(--vigil-color-text-muted);
  overflow-wrap: anywhere;
}

.check-list {
  display: flex;
  flex-direction: column;
  gap: var(--vigil-space-2);
  margin-top: var(--vigil-space-2);
}

.check-card {
  padding: var(--vigil-space-2);
  border: 1px solid var(--vigil-color-border);
  border-radius: var(--vigil-radius-sm);
}

.check-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--vigil-space-2);
  font-size: 11px;
  color: var(--vigil-color-text-muted);
}

.recovery-list {
  display: flex;
  flex-direction: column;
  gap: var(--vigil-space-2);
}

.recovery-row {
  display: flex;
  align-items: center;
  gap: var(--vigil-space-2);
  flex-wrap: wrap;
  font-size: var(--vigil-font-xs);

  .g-input {
    flex: 1 1 130px;
  }

  .field--check {
    flex: 0 0 auto;
  }
}

.recovery-index {
  flex: 0 0 auto;
  color: var(--vigil-color-text-muted);
}

.text-btn {
  border: none;
  background: transparent;
  color: var(--vigil-color-primary);
  font-size: var(--vigil-font-xs);
  cursor: pointer;
}

.text-danger {
  border: none;
  background: transparent;
  color: var(--vigil-color-critical);
  font-size: 11px;
  cursor: pointer;
}

.guard-dialog__foot {
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
  .guard-dialog {
    max-height: 96vh;
  }

  .guard-dialog__body {
    flex-direction: column;
  }

  .guard-nav {
    flex: 0 0 auto;
    max-height: 150px;
    border-right: none;
    border-bottom: 1px solid var(--vigil-color-border);
  }
}
</style>
