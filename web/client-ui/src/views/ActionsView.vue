<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  StatusTag,
  formatCountdown,
  getErrorMessage,
  type ActionExecuteResponse,
  type ActionOption,
} from '@vigil/shared'
import { clientApi } from '@/api/client'

const ACTIONS: ActionOption[] = [
  {
    name: 'ifup_wan',
    label: '重新拨号（ifup wan）',
    description: '通过 ifup wan 重新拉起 WAN 接口，网络会短暂中断',
    risk: 'L1',
  },
  {
    name: 'restart_service',
    label: '重启 DNS 服务（restart_service）',
    description: '重启白名单内的 dnsmasq 服务，仅影响本地解析',
    risk: 'L1',
    target: 'dnsmasq',
  },
]

const DEFAULT_COOLDOWN_SECONDS = 300

const running = ref('')
const cooldowns = reactive<Record<string, number>>({})
const errors = reactive<Record<string, string>>({})
const results = reactive<Record<string, ActionExecuteResponse | null>>({})

let ticker: number | undefined

const busy = computed(() => Boolean(running.value))

function isCooling(name: string): boolean {
  return (cooldowns[name] ?? 0) > 0
}

function buttonLabel(action: ActionOption): string {
  if (running.value === action.name) return '执行中…'
  if (isCooling(action.name)) return `冷却中 ${formatCountdown(cooldowns[action.name])}`
  return '执行'
}

async function execute(action: ActionOption) {
  if (busy.value || isCooling(action.name)) return

  errors[action.name] = ''
  try {
    await ElMessageBox.confirm(
      `${action.label}：${action.description}。执行后可能短时断网，且该动作有 5 分钟冷却，确认继续？`,
      '二次确认',
      { type: 'warning', confirmButtonText: '确认执行', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  running.value = action.name
  try {
    const response = await clientApi.runAction(action.name, action.target)
    results[action.name] = response ?? {}
    const cooldown =
      response?.cooldown_remaining ?? response?.cooldown_seconds ?? DEFAULT_COOLDOWN_SECONDS
    cooldowns[action.name] = Math.max(0, Math.round(Number(cooldown) || 0))
    ElMessage.success(response?.message || '动作已提交执行')
  } catch (err) {
    errors[action.name] = getErrorMessage(err, '动作执行失败')
    ElMessage.error(errors[action.name])
  } finally {
    running.value = ''
  }
}

onMounted(() => {
  ticker = window.setInterval(() => {
    for (const key of Object.keys(cooldowns)) {
      if (cooldowns[key] > 0) cooldowns[key] -= 1
    }
  }, 1000)
})

onBeforeUnmount(() => {
  if (ticker !== undefined) window.clearInterval(ticker)
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">手动动作</h2>
        <p class="page-sub">仅 L1 白名单动作，需二次确认；同一动作冷却 5 分钟</p>
      </div>
    </div>

    <el-alert
      type="warning"
      show-icon
      :closable="false"
      title="动作会修改网络状态，请确认当前无重要业务后再执行"
    />

    <div
      v-for="action in ACTIONS"
      :key="action.name"
      class="panel action-card"
    >
      <div class="panel-body action-body">
        <div class="action-head">
          <div class="action-title-row">
            <strong class="action-title">{{ action.label }}</strong>
            <span class="risk-tag">{{ action.risk }}</span>
          </div>
          <p class="action-desc">{{ action.description }}</p>
          <p class="action-name mono">{{ action.name }}</p>
        </div>
        <el-button
          class="action-btn"
          type="primary"
          size="small"
          :loading="running === action.name"
          :disabled="busy || isCooling(action.name)"
          @click="execute(action)"
        >
          {{ buttonLabel(action) }}
        </el-button>
      </div>

      <p v-if="errors[action.name]" class="action-error">{{ errors[action.name] }}</p>

      <div v-if="results[action.name]" class="action-result">
        <div class="result-head">
          <StatusTag :status="results[action.name]?.result || 'UNKNOWN'" size="sm" />
          <span class="muted">
            退出码 {{ results[action.name]?.exit_code ?? '—' }} · 冷却
            {{ cooldowns[action.name] ?? 0 }}s
          </span>
        </div>
        <pre v-if="results[action.name]?.output" class="result-output">{{ results[action.name]?.output }}</pre>
      </div>
    </div>

    <p class="foot muted">
      冷却与每小时次数由 agent 状态机控制，重复点击不会绕过限制。
    </p>
  </div>
</template>

<style scoped lang="scss">
.action-card {
  overflow: visible;
}

.action-body {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--vigil-space-3);
}

.action-head {
  min-width: 0;
}

.action-title-row {
  display: flex;
  align-items: center;
  gap: var(--vigil-space-2);
}

.action-title {
  font-size: var(--vigil-font-sm);
}

.risk-tag {
  padding: 0 5px;
  border: 1px solid rgba(220, 38, 38, 0.3);
  border-radius: var(--vigil-radius-sm);
  color: var(--vigil-color-critical);
  background: var(--vigil-color-critical-weak);
  font-size: 10px;
  line-height: 16px;
}

.action-desc {
  margin: var(--vigil-space-1) 0 0;
  font-size: var(--vigil-font-xs);
  color: var(--vigil-color-text-secondary);
  line-height: 1.5;
}

.action-name {
  margin: 2px 0 0;
  color: var(--vigil-color-text-muted);
}

.action-btn {
  flex: 0 0 auto;
  min-width: 96px;
}

.action-error {
  margin: 0;
  padding: var(--vigil-space-2) var(--vigil-space-3);
  border-top: 1px solid var(--vigil-color-critical-weak);
  background: var(--vigil-color-critical-weak);
  color: var(--vigil-color-critical);
  font-size: var(--vigil-font-xs);
}

.action-result {
  padding: var(--vigil-space-2) var(--vigil-space-3) var(--vigil-space-3);
  border-top: 1px dashed var(--vigil-color-border);
}

.result-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--vigil-space-2);
  font-size: 11px;
}

.result-output {
  margin: var(--vigil-space-2) 0 0;
  padding: var(--vigil-space-2);
  max-height: 180px;
  overflow: auto;
  background: var(--vigil-color-surface-alt);
  border-radius: var(--vigil-radius-sm);
  font-size: 11px;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.foot {
  margin: 0;
  font-size: 11px;
  text-align: center;
}

@media (max-width: 400px) {
  .action-body {
    flex-direction: column;
  }

  .action-btn {
    width: 100%;
  }
}
</style>
