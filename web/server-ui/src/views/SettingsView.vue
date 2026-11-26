<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getErrorMessage, type PolicySettings } from '@vigil/shared'
import { adminApi } from '@/api/admin'

const form = reactive<PolicySettings>({
  heartbeat_interval: 30,
  suspect_seconds: 90,
  down_seconds: 150,
  beacon_seconds: 180,
  flapping_window_seconds: 1800,
  flapping_threshold: 3,
  actions_enabled: true,
  action_cooldown_seconds: 300,
  action_max_per_hour: 3,
  retention_days: 30,
  disk_alert_percent: 85,
  disk_alert_cooldown_seconds: 21600,
})

const defaults = ref<PolicySettings | null>(null)
const loading = ref(false)
const saving = ref(false)
const error = ref('')

function applyPolicies(policies: PolicySettings) {
  Object.assign(form, policies)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await adminApi.policies()
    defaults.value = data.defaults
    applyPolicies(data.policies)
  } catch (err) {
    error.value = getErrorMessage(err, '策略配置加载失败')
  } finally {
    loading.value = false
  }
}

function validate(): string {
  if (form.heartbeat_interval >= form.suspect_seconds) {
    return 'SUSPECT 阈值必须大于心跳间隔'
  }
  if (form.suspect_seconds >= form.down_seconds) {
    return 'DOWN 阈值必须大于 SUSPECT 阈值'
  }
  if (form.beacon_seconds < form.heartbeat_interval) {
    return 'beacon 阈值不能小于心跳间隔'
  }
  return ''
}

async function save() {
  const message = validate()
  if (message) {
    error.value = message
    return
  }
  saving.value = true
  error.value = ''
  try {
    const data = await adminApi.savePolicies({ ...form })
    applyPolicies(data.policies)
    ElMessage.success('策略已保存，worker 与客户端配置将即时生效')
  } catch (err) {
    error.value = getErrorMessage(err, '策略保存失败')
  } finally {
    saving.value = false
  }
}

function restoreDefaults() {
  if (!defaults.value) return
  applyPolicies(defaults.value)
  ElMessage.info('已填入默认值，点击保存后生效')
}

onMounted(load)
</script>

<template>
  <div v-loading="loading" class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">策略配置</h2>
        <p class="page-sub">保存后无需重启：worker 扫描与客户端下发策略会在下一轮读取新值</p>
      </div>
      <div class="head-actions">
        <el-button size="small" plain @click="restoreDefaults">恢复默认</el-button>
        <el-button size="small" type="primary" :loading="saving" @click="save">保存</el-button>
      </div>
    </div>

    <el-alert v-if="error" class="error-box" type="error" show-icon :closable="false">
      {{ error }}
    </el-alert>

    <section class="panel">
      <div class="panel-body">
        <h3 class="section-title">心跳与离线判定</h3>
        <p class="section-sub">客户端心跳间隔与 SUSPECT / DOWN 判定必须满足：心跳 &lt; SUSPECT &lt; DOWN。</p>
        <div class="grid">
          <label class="field">
            <span class="field-label">心跳间隔（秒）</span>
            <el-input-number v-model="form.heartbeat_interval" :min="10" :max="300" size="small" />
          </label>
          <label class="field">
            <span class="field-label">SUSPECT 阈值（秒）</span>
            <el-input-number v-model="form.suspect_seconds" :min="30" :max="3600" size="small" />
          </label>
          <label class="field">
            <span class="field-label">DOWN 阈值（秒）</span>
            <el-input-number v-model="form.down_seconds" :min="60" :max="7200" size="small" />
          </label>
          <label class="field">
            <span class="field-label">beacon 阈值（秒）</span>
            <el-input-number v-model="form.beacon_seconds" :min="30" :max="3600" size="small" />
          </label>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-body">
        <h3 class="section-title">抖动抑制</h3>
        <p class="section-sub">窗口内同一客户端/守护项恢复次数达到阈值后，抑制单次通知并改发汇总。</p>
        <div class="grid">
          <label class="field">
            <span class="field-label">统计窗口（秒）</span>
            <el-input-number
              v-model="form.flapping_window_seconds"
              :min="300"
              :max="86400"
              :step="300"
              size="small"
            />
          </label>
          <label class="field">
            <span class="field-label">恢复次数阈值</span>
            <el-input-number v-model="form.flapping_threshold" :min="2" :max="20" size="small" />
          </label>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-body">
        <h3 class="section-title">动作与安全上限</h3>
        <p class="section-sub">全局动作开关与客户端 L1 动作冷却/频次上限，随策略下发。</p>
        <div class="grid">
          <label class="field field--switch">
            <span class="field-label">启用动作</span>
            <el-switch v-model="form.actions_enabled" />
          </label>
          <label class="field">
            <span class="field-label">动作冷却（秒）</span>
            <el-input-number
              v-model="form.action_cooldown_seconds"
              :min="0"
              :max="3600"
              :step="30"
              size="small"
            />
          </label>
          <label class="field">
            <span class="field-label">每小时动作上限</span>
            <el-input-number
              v-model="form.action_max_per_hour"
              :min="1"
              :max="100"
              size="small"
            />
          </label>
        </div>
        <p class="section-hint">
          数据保留与磁盘水位已移至
          <router-link class="panel-link" to="/system">系统设置</router-link>。
        </p>
      </div>
    </section>
  </div>
</template>

<style scoped lang="scss">
.head-actions {
  display: flex;
  gap: var(--vigil-space-2);
}

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

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: var(--vigil-space-3) var(--vigil-space-4);
}

.field {
  display: flex;
  flex-direction: column;
  gap: var(--vigil-space-1);

  &--switch {
    justify-content: space-between;
  }
}

.field-label {
  color: var(--vigil-color-text-muted);
  font-size: var(--vigil-font-sm);
}

.section-hint {
  margin: var(--vigil-space-3) 0 0;
  font-size: var(--vigil-font-xs);
  color: var(--vigil-color-text-muted);
}

.panel-link {
  color: var(--vigil-color-primary);
}
</style>
