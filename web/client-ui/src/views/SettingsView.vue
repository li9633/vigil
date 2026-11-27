<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import {
  LoadingBlock,
  StatusTag,
  TimeText,
  getErrorMessage,
  useTheme,
  type ClientSettingsSnapshot,
  type ThemeMode,
} from '@vigil/shared'
import { ElMessage } from 'element-plus'
import { clientApi } from '@/api/client'

const data = ref<ClientSettingsSnapshot | null>(null)
const loading = ref(false)
const saving = ref(false)
const maintenanceBusy = ref(false)
const error = ref('')

const form = reactive({
  baseUrl: '',
  heartbeat: 30,
  enabled: true,
  maxPerHour: 3,
})

function hydrate(snapshot: ClientSettingsSnapshot) {
  data.value = snapshot
  form.baseUrl = snapshot.server.base_url
  form.heartbeat = snapshot.server.heartbeat_interval
  form.enabled = snapshot.action_safety.global_enabled
  form.maxPerHour = snapshot.action_safety.max_actions_per_hour
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

onMounted(load)
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
            <p class="panel-sub">地址修改后立即生效，并会重新拉取服务端策略</p>
          </div>
          <StatusTag
            :status="data.server.registered ? 'OK' : 'UNKNOWN'"
            :label="data.server.registered ? '已注册' : '未注册'"
            size="sm"
          />
        </div>
        <div class="panel-body">
          <div class="form-grid">
            <label class="field">
              <span class="field-label">服务端地址</span>
              <el-input v-model="form.baseUrl" placeholder="http://vigil.example.com:5000" />
            </label>
            <label class="field">
              <span class="field-label">心跳间隔（秒）</span>
              <el-input-number v-model="form.heartbeat" :min="10" :max="300" :step="10" />
            </label>
          </div>
          <div class="meta-row">
            <span>client_id <span class="mono">{{ data.server.client_id || '未分配' }}</span></span>
            <span>配置同步 {{ data.server.config_poll_interval }}s</span>
            <span>证书校验 {{ data.server.verify_tls ? '开启' : '关闭' }}</span>
          </div>
          <el-alert class="hint" type="info" :closable="false" show-icon>
            若地址不可达，客户端将无法接收服务端策略，但本地检测与自动恢复仍会运行。
          </el-alert>
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
