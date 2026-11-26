<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  TimeText,
  getErrorMessage,
  type AdminSystemSettings,
  type PolicySettings,
} from '@vigil/shared'
import { adminApi } from '@/api/admin'

const system = ref<AdminSystemSettings | null>(null)
const loading = ref(false)
const error = ref('')

const passwordForm = reactive({ current: '', next: '', confirm: '' })
const passwordSaving = ref(false)

const retentionForm = reactive({
  retention_days: 30,
  disk_alert_percent: 85,
  disk_alert_cooldown_seconds: 21600,
})
const retentionSaving = ref(false)

function fillRetention(policies: PolicySettings) {
  retentionForm.retention_days = policies.retention_days
  retentionForm.disk_alert_percent = policies.disk_alert_percent
  retentionForm.disk_alert_cooldown_seconds = policies.disk_alert_cooldown_seconds
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [systemData, policyData] = await Promise.all([
      adminApi.system(),
      adminApi.policies(),
    ])
    system.value = systemData
    fillRetention(policyData.policies)
  } catch (err) {
    error.value = getErrorMessage(err, '系统设置加载失败')
  } finally {
    loading.value = false
  }
}

function validatePassword(): string {
  if (!passwordForm.current) return '请输入当前密码'
  if (passwordForm.next.length < 8) return '新密码至少 8 位'
  if (passwordForm.next.length > 128) return '新密码最多 128 位'
  if (passwordForm.next !== passwordForm.confirm) return '两次输入的新密码不一致'
  if (passwordForm.next === passwordForm.current) return '新密码不能与当前密码相同'
  return ''
}

async function changePassword() {
  const message = validatePassword()
  if (message) {
    error.value = message
    return
  }
  passwordSaving.value = true
  error.value = ''
  try {
    const result = await adminApi.changeAdminPassword({
      current_password: passwordForm.current,
      new_password: passwordForm.next,
    })
    if (system.value) {
      system.value.password_updated_at = result.password_updated_at
    }
    passwordForm.current = ''
    passwordForm.next = ''
    passwordForm.confirm = ''
    ElMessage.success('管理员密码已更新，下次登录请使用新密码')
  } catch (err) {
    error.value = getErrorMessage(err, '密码修改失败')
  } finally {
    passwordSaving.value = false
  }
}

async function saveRetention() {
  retentionSaving.value = true
  error.value = ''
  try {
    const data = await adminApi.savePolicies({ ...retentionForm })
    fillRetention(data.policies)
    ElMessage.success('已保存，worker 下一轮清理与磁盘检查即生效')
  } catch (err) {
    error.value = getErrorMessage(err, '保存失败')
  } finally {
    retentionSaving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div v-loading="loading" class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">系统设置</h2>
        <p class="page-sub">管理员凭证、数据保留与磁盘水位；保存后即时生效，无需重启服务</p>
      </div>
    </div>

    <el-alert v-if="error" class="error-box" type="error" show-icon :closable="false">
      {{ error }}
    </el-alert>

    <section class="panel">
      <div class="panel-body">
        <h3 class="section-title">管理员账号</h3>
        <p class="section-sub">
          用户名由环境变量固定；密码以 PBKDF2 哈希保存在数据库中，修改时需验证当前密码。
          首次启动的初始密码见服务器 /var/lib/vigil/initial_admin_password，修改后会删除。
        </p>
        <div class="meta-line">
          <span class="muted">用户名</span>
          <strong class="mono">{{ system?.admin_username || '—' }}</strong>
          <span v-if="system?.password_updated_at" class="muted">
            更新于 <TimeText :value="system.password_updated_at" mode="datetime" />
          </span>
        </div>
        <div class="grid password-grid">
          <label class="field">
            <span class="field-label">当前密码</span>
            <el-input
              v-model="passwordForm.current"
              type="password"
              show-password
              autocomplete="current-password"
              size="small"
            />
          </label>
          <label class="field">
            <span class="field-label">新密码（至少 8 位）</span>
            <el-input
              v-model="passwordForm.next"
              type="password"
              show-password
              autocomplete="new-password"
              size="small"
            />
          </label>
          <label class="field">
            <span class="field-label">确认新密码</span>
            <el-input
              v-model="passwordForm.confirm"
              type="password"
              show-password
              autocomplete="new-password"
              size="small"
            />
          </label>
        </div>
        <div class="form-actions">
          <el-button size="small" type="primary" :loading="passwordSaving" @click="changePassword">
            修改密码
          </el-button>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-body">
        <h3 class="section-title">数据保留与磁盘水位</h3>
        <p class="section-sub">
          保留天数仅影响心跳、beacon 与事件明细；磁盘使用率超过阈值时发送系统告警。
        </p>
        <div class="grid">
          <label class="field">
            <span class="field-label">明细保留（天，0 为不清理）</span>
            <el-input-number
              v-model="retentionForm.retention_days"
              :min="0"
              :max="3650"
              size="small"
            />
          </label>
          <label class="field">
            <span class="field-label">磁盘告警阈值（%）</span>
            <el-input-number
              v-model="retentionForm.disk_alert_percent"
              :min="50"
              :max="99"
              size="small"
            />
          </label>
          <label class="field">
            <span class="field-label">磁盘告警冷却（秒）</span>
            <el-input-number
              v-model="retentionForm.disk_alert_cooldown_seconds"
              :min="300"
              :max="86400"
              :step="300"
              size="small"
            />
          </label>
        </div>
        <div class="form-actions">
          <el-button size="small" type="primary" :loading="retentionSaving" @click="saveRetention">
            保存
          </el-button>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-body">
        <h3 class="section-title">客户端密钥</h3>
        <p class="section-sub">
          secret 仅存哈希，无法查看明文。需要更换时请在客户端详情页轮换，新 secret 只显示一次，
          并需同步更新设备上的配置。
        </p>
        <router-link class="panel-link" to="/clients">前往客户端列表 →</router-link>
      </div>
    </section>
  </div>
</template>

<style scoped lang="scss">
.section-title {
  margin: 0 0 var(--vigil-space-1);
  font-size: var(--vigil-font-md);
  font-weight: 600;
}

.section-sub {
  margin: 0 0 var(--vigil-space-3);
  color: var(--vigil-color-text-muted);
  font-size: var(--vigil-font-sm);
}

.meta-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--vigil-space-2);
  margin-bottom: var(--vigil-space-3);
  font-size: var(--vigil-font-sm);
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: var(--vigil-space-3) var(--vigil-space-4);
}

.password-grid {
  max-width: 720px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: var(--vigil-space-1);
}

.field-label {
  color: var(--vigil-color-text-muted);
  font-size: var(--vigil-font-sm);
}

.form-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: var(--vigil-space-3);
}

.panel-link {
  font-size: var(--vigil-font-sm);
  color: var(--vigil-color-primary);
}
</style>
