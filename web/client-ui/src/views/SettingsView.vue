<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import {
  LoadingBlock,
  StatusTag,
  TimeText,
  copyText,
  getErrorMessage,
  useTheme,
  type ClientPairingState,
  type ClientSettingsSnapshot,
  type ThemeMode,
} from '@vigil/shared'
import { ElMessage, ElMessageBox } from 'element-plus'
import { clientApi } from '@/api/client'

const data = ref<ClientSettingsSnapshot | null>(null)
const loading = ref(false)
const saving = ref(false)
const maintenanceBusy = ref(false)
const pairingBusy = ref(false)
const unregisterBusy = ref(false)
const error = ref('')
const pairing = ref<ClientPairingState | null>(null)
const nowSeconds = ref(Math.floor(Date.now() / 1000))
let pollTimer: number | undefined
let clockTimer: number | undefined

const form = reactive({
  baseUrl: '',
  heartbeat: 30,
  enabled: true,
  maxPerHour: 3,
})

const pairingPending = computed(() => pairing.value?.status === 'pending')
const codeRemaining = computed(() =>
  Math.max(0, (pairing.value?.code_expires_at ?? 0) - nowSeconds.value),
)
const pairingLevel = computed(() => {
  if (data.value?.server.registered) return 'OK'
  if (pairingPending.value) return 'WARN'
  return 'UNKNOWN'
})
const pairingLabel = computed(() => {
  if (data.value?.server.registered) return '已配对'
  switch (pairing.value?.status) {
    case 'pending':
      return '等待确认'
    case 'code_expired':
      return '配对码已过期'
    case 'denied':
      return '已拒绝'
    case 'expired':
      return '已过期'
    default:
      return '未配对'
  }
})

function hydrate(snapshot: ClientSettingsSnapshot) {
  data.value = snapshot
  form.baseUrl = snapshot.server.base_url
  form.heartbeat = snapshot.server.heartbeat_interval
  form.enabled = snapshot.action_safety.global_enabled
  form.maxPerHour = snapshot.action_safety.max_actions_per_hour
  pairing.value = snapshot.pairing ?? null
  if (pairingPending.value) {
    startPolling()
  } else {
    stopPolling()
  }
}

function formatRemaining(seconds: number) {
  const minutes = Math.floor(seconds / 60)
  return `${minutes}:${String(seconds % 60).padStart(2, '0')}`
}

function startPolling() {
  if (pollTimer !== undefined) return
  pollTimer = window.setInterval(pollPairing, 3000)
}

function stopPolling() {
  if (pollTimer === undefined) return
  window.clearInterval(pollTimer)
  pollTimer = undefined
}

async function pollPairing() {
  if (!pairingPending.value) {
    stopPolling()
    return
  }
  try {
    const snapshot = await clientApi.pollPairing()
    hydrate(snapshot)
    if (snapshot.server.registered) {
      ElMessage.success('配对成功，客户端已注册')
    }
  } catch (err) {
    pairing.value = {
      ...(pairing.value as ClientPairingState),
      last_error: getErrorMessage(err, '轮询配对状态失败'),
    }
  }
}

async function startPairing() {
  if (pairingBusy.value) return
  const baseUrl = form.baseUrl.trim()
  if (!/^https?:\/\/\S+$/i.test(baseUrl)) {
    ElMessage.warning('请先填写正确的服务端地址')
    return
  }
  pairingBusy.value = true
  try {
    hydrate(await clientApi.startPairing(baseUrl))
    ElMessage.success('已发起配对，请在服务端输入配对码确认')
  } catch (err) {
    ElMessage.error(getErrorMessage(err, '发起配对失败'))
  } finally {
    pairingBusy.value = false
  }
}

async function cancelPairing() {
  pairingBusy.value = true
  try {
    hydrate(await clientApi.cancelPairing())
    ElMessage.info('已取消本地配对')
  } catch (err) {
    ElMessage.error(getErrorMessage(err, '取消配对失败'))
  } finally {
    pairingBusy.value = false
  }
}

async function unregister() {
  if (unregisterBusy.value) return
  try {
    await ElMessageBox.confirm(
      '取消后本地凭据会立即清除并停止上报；服务端会将该设备标记为失效，设备记录需要管理员在服务端手动删除。确定继续？',
      '取消注册',
      { type: 'warning', confirmButtonText: '取消注册', cancelButtonText: '返回' },
    )
  } catch {
    return
  }
  unregisterBusy.value = true
  try {
    const result = await clientApi.unregister()
    hydrate(result.settings)
    if (result.server_notified) {
      ElMessage.success('已取消注册')
    } else {
      ElMessage.warning(
        `本地已取消注册，但服务端未确认：${result.server_error || '请管理员手动清理服务端记录'}`,
      )
    }
  } catch (err) {
    ElMessage.error(getErrorMessage(err, '取消注册失败'))
  } finally {
    unregisterBusy.value = false
  }
}

async function copyPairingCode() {
  const code = (pairing.value?.code || '').replace('-', '')
  if (!code) return
  if (await copyText(code)) {
    ElMessage.success('配对码已复制')
    return
  }
  ElMessage.warning('复制失败，请手动选择配对码')
}

async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    hydrate(await clientApi.settings())
  } catch (err) {
    error.value = getErrorMessage(err, '设置加载失败')
  } finally {
    loading.value = false
  }
}

async function save() {
  if (saving.value) return
  const baseUrl = form.baseUrl.trim()
  if (!/^https?:\/\/\S+$/i.test(baseUrl)) {
    ElMessage.warning('服务端地址必须是 http(s) URL')
    return
  }
  saving.value = true
  try {
    hydrate(
      await clientApi.saveSettings({
        server: { base_url: baseUrl },
        heartbeat_interval: form.heartbeat,
        action_safety: {
          global_enabled: form.enabled,
          max_risk_level: 'L1',
          max_actions_per_hour: form.maxPerHour,
        },
      }),
    )
    ElMessage.success('设置已保存')
  } catch (err) {
    ElMessage.error(getErrorMessage(err, '设置保存失败'))
  } finally {
    saving.value = false
  }
}

async function setMaintenance(minutes: number) {
  if (maintenanceBusy.value) return
  maintenanceBusy.value = true
  try {
    hydrate(await clientApi.saveSettings({ maintenance_minutes: minutes }))
    ElMessage.success(minutes > 0 ? `已进入 ${minutes} 分钟维护窗口` : '已取消维护窗口')
  } catch (err) {
    ElMessage.error(getErrorMessage(err, '维护窗口设置失败'))
  } finally {
    maintenanceBusy.value = false
  }
}

const { mode, resolved, setTheme } = useTheme()

function changeTheme(value: string | number | boolean) {
  setTheme(String(value) as ThemeMode)
}

onMounted(() => {
  load()
  clockTimer = window.setInterval(() => {
    nowSeconds.value = Math.floor(Date.now() / 1000)
  }, 1000)
})

onUnmounted(() => {
  stopPolling()
  if (clockTimer !== undefined) window.clearInterval(clockTimer)
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">设置</h2>
        <p class="page-sub">服务端连接、动作安全上限与维护窗口</p>
      </div>
      <el-button type="primary" size="small" :loading="saving" @click="save">保存设置</el-button>
    </div>

    <el-alert v-if="error" class="error-box" type="error" show-icon :closable="false">
      <div class="alert-row">
        <span>{{ error }}</span>
        <el-button size="small" text type="primary" @click="load">重试</el-button>
      </div>
    </el-alert>

    <LoadingBlock v-if="loading && !data" min-height="180px" text="正在读取设置…" />

    <template v-else-if="data">
      <div class="panel">
        <div class="panel-head">
          <div>
            <h3 class="panel-title">外观</h3>
            <p class="panel-sub">默认跟随系统；顶栏按钮可快速切换深色 / 浅色</p>
          </div>
          <span class="muted">当前：{{ resolved === 'dark' ? '深色' : '浅色' }}</span>
        </div>
        <div class="panel-body">
          <el-radio-group :model-value="mode" size="small" @change="changeTheme">
            <el-radio-button value="auto">跟随系统</el-radio-button>
            <el-radio-button value="light">浅色</el-radio-button>
            <el-radio-button value="dark">深色</el-radio-button>
          </el-radio-group>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <div>
            <h3 class="panel-title">服务端连接</h3>
            <p class="panel-sub">
              填写地址后点击「立即注册」，把配对码交给服务端管理员确认即可，无需手动配置令牌
            </p>
          </div>
          <StatusTag :status="pairingLevel" :label="pairingLabel" size="sm" />
        </div>
        <div class="panel-body">
          <div class="form-grid">
            <label class="field">
              <span class="field-label">服务端地址</span>
              <el-input
                v-model="form.baseUrl"
                placeholder="http://vigil.example.com:5000"
                :disabled="data.server.registered"
              />
            </label>
            <label class="field">
              <span class="field-label">心跳间隔（秒）</span>
              <el-input-number v-model="form.heartbeat" :min="10" :max="300" :step="10" />
            </label>
            <div class="field">
              <span class="field-label">配对</span>
              <el-button
                type="primary"
                :loading="pairingBusy"
                :disabled="data.server.registered"
                @click="startPairing"
              >
                {{ pairingPending ? '重新生成配对码' : '立即注册' }}
              </el-button>
            </div>
          </div>

          <div v-if="pairingPending" class="pair-box">
            <div>
              <div class="pair-label">配对码</div>
              <div class="pair-code mono">{{ pairing?.code }}</div>
            </div>
            <div class="pair-side">
              <span class="muted">剩余 {{ formatRemaining(codeRemaining) }}</span>
              <div class="actions">
                <el-button size="small" @click="copyPairingCode">复制</el-button>
                <el-button size="small" type="danger" plain :loading="pairingBusy" @click="cancelPairing">
                  取消
                </el-button>
              </div>
            </div>
          </div>

          <el-alert v-if="pairingPending" class="hint" type="info" :closable="false" show-icon>
            在服务端「配对审批」中找到本设备，输入上面的配对码并确认；成功后本页会自动刷新为已注册。
          </el-alert>
          <el-alert
            v-else-if="pairing && ['code_expired', 'expired', 'denied'].includes(pairing.status)"
            class="hint"
            type="warning"
            :closable="false"
            show-icon
          >
            {{ pairing.last_error || '配对未完成，请重新发起注册' }}
          </el-alert>
          <el-alert
            v-else-if="!data.server.registered"
            class="hint"
            type="info"
            :closable="false"
            show-icon
          >
            未配对时无法接收服务端策略，但本地检测与自动恢复仍会运行。
          </el-alert>

          <div class="meta-row">
            <span>client_id <span class="mono">{{ data.server.client_id || '未分配' }}</span></span>
            <span>配置同步 {{ data.server.config_poll_interval }}s</span>
            <span>证书校验 {{ data.server.verify_tls ? '开启' : '关闭' }}</span>
          </div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <div>
            <h3 class="panel-title">全局动作安全上限</h3>
            <p class="panel-sub">对所有守护项的恢复动作生效，优先级高于本地配置中的旧设置</p>
          </div>
        </div>
        <div class="panel-body">
          <div class="form-grid">
            <label class="field">
              <span class="field-label">启用自动动作</span>
              <el-switch v-model="form.enabled" active-text="启用" inactive-text="停用" />
            </label>
            <label class="field">
              <span class="field-label">每小时动作上限</span>
              <el-input-number v-model="form.maxPerHour" :min="1" :max="60" />
            </label>
            <label class="field">
              <span class="field-label">最高风险级别</span>
              <el-input :model-value="data.action_safety.max_risk_level" disabled />
            </label>
          </div>
          <div class="meta-row">
            <span>
              服务白名单
              <template v-if="data.action_safety.allowed_services.length">
                <span
                  v-for="service in data.action_safety.allowed_services"
                  :key="service"
                  class="service-tag mono"
                >
                  {{ service }}
                </span>
              </template>
              <span v-else>未配置</span>
            </span>
          </div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <div>
            <h3 class="panel-title">维护窗口</h3>
            <p class="panel-sub">窗口内禁止所有自动动作，适合远程维护或已知的断网演练</p>
          </div>
          <StatusTag
            :status="data.maintenance.active ? 'WARN' : 'OK'"
            :label="data.maintenance.active ? '维护中' : '未启用'"
            size="sm"
          />
        </div>
        <div class="panel-body">
          <p class="meta-row">
            <template v-if="data.maintenance.active">
              截止 <TimeText :value="data.maintenance.until" mode="datetime" />
              · 剩余约 {{ Math.ceil(data.maintenance.remaining_seconds / 60) }} 分钟
            </template>
            <template v-else>当前没有生效的维护窗口</template>
          </p>
          <div class="actions">
            <el-button size="small" :loading="maintenanceBusy" @click="setMaintenance(30)">
              30 分钟
            </el-button>
            <el-button size="small" :loading="maintenanceBusy" @click="setMaintenance(60)">
              60 分钟
            </el-button>
            <el-button size="small" :loading="maintenanceBusy" @click="setMaintenance(120)">
              120 分钟
            </el-button>
            <el-button
              size="small"
              type="danger"
              plain
              :loading="maintenanceBusy"
              :disabled="!data.maintenance.active"
              @click="setMaintenance(0)"
            >
              取消维护
            </el-button>
          </div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <div>
            <h3 class="panel-title">取消注册</h3>
            <p class="panel-sub">
              清除本地凭据并停止上报；服务端会将该设备标记为失效，记录需管理员在服务端手动删除
            </p>
          </div>
          <el-button
            type="danger"
            plain
            size="small"
            :loading="unregisterBusy"
            :disabled="!data.server.registered"
            @click="unregister"
          >
            取消注册
          </el-button>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <div>
            <h3 class="panel-title">版本信息</h3>
            <p class="panel-sub">客户端与协议版本，只读</p>
          </div>
        </div>
        <div class="panel-body">
          <dl class="kv">
            <div><dt>Agent 版本</dt><dd class="mono">{{ data.version }}</dd></div>
            <div><dt>协议版本</dt><dd class="mono">{{ data.protocol_version ?? '—' }}</dd></div>
            <div><dt>配置文件</dt><dd class="mono">{{ data.config_path || '—' }}</dd></div>
            <div>
              <dt>守护项来源</dt>
              <dd>{{ data.guards_source === 'server' ? '服务端托管' : '本地配置' }}</dd>
            </div>
          </dl>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped lang="scss">
.panel-sub {
  margin: 4px 0 0;
  font-size: var(--vigil-font-xxs);
  color: var(--vigil-color-text-muted);
}

.alert-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--vigil-space-2);
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--vigil-space-3);
}

.field {
  display: flex;
  flex-direction: column;
  gap: var(--vigil-space-1);
}

.field-label {
  color: var(--vigil-color-text-muted);
  font-size: var(--vigil-font-size-sm);
}

.meta-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--vigil-space-3);
  margin-top: var(--vigil-space-3);
  color: var(--vigil-color-text-muted);
  font-size: var(--vigil-font-size-sm);
}

.hint {
  margin-top: var(--vigil-space-3);
}

.pair-box {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--vigil-space-3);
  margin-top: var(--vigil-space-3);
  padding: var(--vigil-space-3);
  border: 1px dashed var(--vigil-color-border);
  border-radius: var(--vigil-radius-md);
}

.pair-label {
  color: var(--vigil-color-text-muted);
  font-size: var(--vigil-font-size-sm);
}

.pair-code {
  font-size: 28px;
  font-weight: 600;
  letter-spacing: 4px;
}

.pair-side {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: var(--vigil-space-1);
}

.service-tag {
  display: inline-block;
  margin-left: 4px;
  padding: 0 4px;
  border: 1px solid var(--vigil-color-border);
  border-radius: var(--vigil-radius-sm);
  color: var(--vigil-color-text);
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--vigil-space-2);
  margin-top: var(--vigil-space-3);
}

.kv {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--vigil-space-3);
  margin: 0;

  div {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  dt {
    color: var(--vigil-color-text-muted);
    font-size: var(--vigil-font-size-sm);
  }

  dd {
    margin: 0;
    word-break: break-all;
  }
}
</style>
