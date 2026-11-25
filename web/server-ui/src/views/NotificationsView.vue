<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  EmptyState,
  TimeText,
  getErrorMessage,
  type NotificationChannelKind,
  type NotificationRecord,
  type NotificationSettings,
  type NotificationTestResult,
} from '@vigil/shared'
import { adminApi } from '@/api/admin'

interface EmailForm {
  enabled: boolean
  smtp_host: string
  smtp_port: number
  username: string
  password: string
  from: string
  to: string
  security: string
}

interface WebhookForm {
  enabled: boolean
  webhook: string
}

interface ConfigForm {
  feishu: WebhookForm
  wecom: WebhookForm
  email: EmailForm
  rate_limit_per_hour: number
}

const SECURITY_OPTIONS = [
  { label: 'SSL（465）', value: 'ssl' },
  { label: 'STARTTLS（587）', value: 'starttls' },
  { label: '无加密', value: 'none' },
]

const WEBHOOK_CHANNELS: Array<{ key: 'feishu' | 'wecom'; label: string; placeholder: string }> = [
  {
    key: 'feishu',
    label: '飞书',
    placeholder: 'https://open.feishu.cn/open-apis/bot/v2/hook/...',
  },
  {
    key: 'wecom',
    label: '企业微信',
    placeholder: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...',
  },
]

function emptyForm(): ConfigForm {
  return {
    feishu: { enabled: false, webhook: '' },
    wecom: { enabled: false, webhook: '' },
    email: {
      enabled: false,
      smtp_host: '',
      smtp_port: 465,
      username: '',
      password: '',
      from: '',
      to: '',
      security: 'ssl',
    },
    rate_limit_per_hour: 10,
  }
}

function toForm(settings?: Partial<NotificationSettings> | null): ConfigForm {
  const form = emptyForm()
  if (!settings) return form

  const feishu = settings.feishu ?? {}
  const wecom = settings.wecom ?? {}
  const email = settings.email ?? {}

  return {
    feishu: { enabled: Boolean(feishu.enabled), webhook: feishu.webhook ?? '' },
    wecom: { enabled: Boolean(wecom.enabled), webhook: wecom.webhook ?? '' },
    email: {
      enabled: Boolean(email.enabled),
      smtp_host: email.smtp_host ?? '',
      smtp_port: Number(email.smtp_port) || 465,
      username: email.username ?? '',
      password: email.password ?? '',
      from: email.from ?? '',
      to: Array.isArray(email.to) ? email.to.join(',') : (email.to ?? ''),
      security: email.security ?? 'ssl',
    },
    rate_limit_per_hour:
      typeof settings.rate_limit_per_hour === 'number' ? settings.rate_limit_per_hour : 10,
  }
}

function toPayload(source: ConfigForm): NotificationSettings {
  return {
    feishu: { enabled: source.feishu.enabled, webhook: source.feishu.webhook },
    wecom: { enabled: source.wecom.enabled, webhook: source.wecom.webhook },
    email: {
      enabled: source.email.enabled,
      smtp_host: source.email.smtp_host,
      smtp_port: source.email.smtp_port,
      username: source.email.username,
      password: source.email.password,
      from: source.email.from,
      to: source.email.to,
      security: source.email.security,
    },
    rate_limit_per_hour: source.rate_limit_per_hour,
  }
}

const form = reactive<ConfigForm>(emptyForm())
const records = ref<NotificationRecord[]>([])
const loading = ref(false)
const saving = ref(false)
const recordsLoading = ref(false)
const error = ref('')
const recordsError = ref('')
const testing = reactive<Record<string, boolean>>({})
const testResults = reactive<Record<string, NotificationTestResult | null>>({})

const CHANNEL_LABELS: Record<string, string> = {
  feishu: '飞书',
  wecom: '企业微信',
  email: '邮件',
}

const KIND_LABELS: Record<string, string> = {
  down: '离线',
  recovery: '恢复',
  test: '测试',
}

const STATUS_LABELS: Record<string, string> = {
  sent: '已发送',
  failed: '失败',
  skipped: '已跳过',
  pending: '待发送',
}

function channelLabel(channel: string | null | undefined): string {
  if (!channel) return '—'
  return CHANNEL_LABELS[channel] ?? channel
}

function kindLabel(kind: string | null | undefined): string {
  if (!kind) return '—'
  return KIND_LABELS[kind] ?? kind
}

function statusLabel(status: string | null | undefined): string {
  if (!status) return '—'
  return STATUS_LABELS[status] ?? status
}

function statusTagType(status: string): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'sent') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'skipped') return 'info'
  return 'warning'
}

function resultFor(channel: string): NotificationTestResult | null {
  return testResults[channel] ?? null
}

function resultText(result: NotificationTestResult): string {
  return result.ok
    ? `${channelLabel(result.channel)} 测试发送成功`
    : `${channelLabel(result.channel)} 测试失败：${result.error || '未知错误'}`
}

async function loadRecords() {
  if (recordsLoading.value) return
  recordsLoading.value = true
  recordsError.value = ''
  try {
    const data = await adminApi.notificationRecords(100)
    records.value = data.records ?? []
  } catch (err) {
    recordsError.value = getErrorMessage(err, '通知记录加载失败')
  } finally {
    recordsLoading.value = false
  }
}

async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const data = await adminApi.notifications()
    Object.assign(form, toForm(data.config))
    records.value = data.records ?? []
  } catch (err) {
    error.value = getErrorMessage(err, '通知配置加载失败')
  } finally {
    loading.value = false
  }
}

async function save() {
  if (saving.value) return
  saving.value = true
  error.value = ''
  try {
    const data = await adminApi.saveNotifications(toPayload(form))
    Object.assign(form, toForm(data.config))
    ElMessage.success('通知配置已保存')
  } catch (err) {
    ElMessage.error(getErrorMessage(err, '保存失败，请稍后重试'))
  } finally {
    saving.value = false
  }
}

async function testChannel(channel: NotificationChannelKind) {
  if (testing[channel]) return
  testing[channel] = true
  testResults[channel] = null
  try {
    const data = await adminApi.testNotification(channel)
    const first = data.results?.[0] ?? null
    testResults[channel] = first
    if (first?.ok) {
      ElMessage.success(`${channelLabel(channel)} 测试发送成功`)
      loadRecords()
    } else {
      ElMessage.error(first?.error || '测试发送失败')
    }
  } catch (err) {
    const message = getErrorMessage(err, '测试发送失败')
    testResults[channel] = { channel, ok: false, error: message }
    ElMessage.error(message)
  } finally {
    testing[channel] = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">通知配置</h2>
        <p class="page-sub">
          离线 / 恢复通知按启用的渠道发送；密钥类字段展示为掩码，以 **** 开头表示保持不变
        </p>
      </div>
      <div class="toolbar">
        <span class="rate-limit">
          <span class="muted">每小时上限</span>
          <el-input-number
            v-model="form.rate_limit_per_hour"
            :min="0"
            :max="10000"
            size="small"
            controls-position="right"
          />
        </span>
        <el-button size="small" :loading="loading" @click="load">刷新</el-button>
        <el-button
          size="small"
          type="primary"
          :loading="saving"
          :disabled="saving"
          @click="save"
        >
          保存配置
        </el-button>
      </div>
    </div>

    <el-alert v-if="error" type="error" show-icon :closable="false">
      <div class="error-actions">
        <span>{{ error }}</span>
        <el-button size="small" text type="primary" @click="load">重试</el-button>
      </div>
    </el-alert>

    <div class="channel-grid">
      <section v-for="item in WEBHOOK_CHANNELS" :key="item.key" class="panel channel-card">
        <div class="panel-head">
          <span class="panel-title">{{ item.label }}</span>
          <el-switch v-model="form[item.key].enabled" size="small" />
        </div>
        <div class="panel-body">
          <el-form label-position="top" size="small" class="channel-form">
            <el-form-item label="Webhook 地址">
              <el-input v-model="form[item.key].webhook" :placeholder="item.placeholder" clearable />
              <p class="field-hint">以 **** 开头表示保持不变</p>
            </el-form-item>
          </el-form>
          <div class="channel-actions">
            <el-button
              size="small"
              :loading="testing[item.key]"
              :disabled="!form[item.key].enabled"
              @click="testChannel(item.key)"
            >
              测试发送
            </el-button>
            <span v-if="!form[item.key].enabled" class="muted field-hint">启用后可测试</span>
          </div>
          <el-alert
            v-if="resultFor(item.key)"
            class="test-result"
            :type="resultFor(item.key)?.ok ? 'success' : 'error'"
            show-icon
            :closable="false"
            :title="resultText(resultFor(item.key) as NotificationTestResult)"
          />
        </div>
      </section>

      <section class="panel channel-card">
        <div class="panel-head">
          <span class="panel-title">邮件</span>
          <el-switch v-model="form.email.enabled" size="small" />
        </div>
        <div class="panel-body">
          <el-form label-position="top" size="small" class="channel-form">
            <div class="form-row">
              <el-form-item label="SMTP 主机">
                <el-input v-model="form.email.smtp_host" placeholder="smtp.example.com" clearable />
              </el-form-item>
              <el-form-item label="端口">
                <el-input-number
                  v-model="form.email.smtp_port"
                  :min="1"
                  :max="65535"
                  size="small"
                  controls-position="right"
                  class="port-input"
                />
              </el-form-item>
            </div>
            <el-form-item label="安全模式">
              <el-select v-model="form.email.security" class="full-width">
                <el-option
                  v-for="option in SECURITY_OPTIONS"
                  :key="option.value"
                  :label="option.label"
                  :value="option.value"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="用户名（多数邮箱必填）">
              <el-input
                v-model="form.email.username"
                placeholder="如 admin@example.com"
                clearable
              />
              <p class="field-hint">QQ、163 等邮箱需填写完整邮箱地址</p>
            </el-form-item>
            <el-form-item label="密码 / 授权码">
              <el-input
                v-model="form.email.password"
                type="password"
                show-password
                placeholder="QQ、163 等需填授权码，不是登录密码"
              />
              <p class="field-hint">以 **** 开头表示保持不变</p>
            </el-form-item>
            <el-form-item label="发件人">
              <el-input v-model="form.email.from" placeholder="vigil@example.com" clearable />
            </el-form-item>
            <el-form-item label="收件人">
              <el-input
                v-model="form.email.to"
                placeholder="多个收件人用逗号分隔"
                clearable
              />
            </el-form-item>
          </el-form>
          <div class="channel-actions">
            <el-button
              size="small"
              :loading="testing.email"
              :disabled="!form.email.enabled"
              @click="testChannel('email')"
            >
              测试发送
            </el-button>
            <span v-if="!form.email.enabled" class="muted field-hint">启用后可测试</span>
          </div>
          <el-alert
            v-if="resultFor('email')"
            class="test-result"
            :type="resultFor('email')?.ok ? 'success' : 'error'"
            show-icon
            :closable="false"
            :title="resultText(resultFor('email') as NotificationTestResult)"
          />
        </div>
      </section>
    </div>

    <div class="panel">
      <div class="panel-head">
        <span class="panel-title">最近通知记录</span>
        <el-button size="small" :loading="recordsLoading" @click="loadRecords">刷新</el-button>
      </div>

      <el-alert
        v-if="recordsError"
        class="table-error"
        type="error"
        show-icon
        :closable="false"
      >
        <div class="error-actions">
          <span>{{ recordsError }}</span>
          <el-button size="small" text type="primary" @click="loadRecords">重试</el-button>
        </div>
      </el-alert>

      <div class="panel-body panel-body--flush">
        <el-table v-loading="recordsLoading" :data="records" row-key="id">
          <el-table-column label="时间" width="160">
            <template #default="{ row }">
              <TimeText :value="row.created_at" mode="datetime" />
            </template>
          </el-table-column>
          <el-table-column label="渠道" width="100">
            <template #default="{ row }">{{ channelLabel(row.channel) }}</template>
          </el-table-column>
          <el-table-column label="类型" width="90">
            <template #default="{ row }">{{ kindLabel(row.kind) }}</template>
          </el-table-column>
          <el-table-column label="状态" width="102">
            <template #default="{ row }">
              <el-tag :type="statusTagType(row.status)" effect="plain" size="small">
                {{ statusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="目标" min-width="150">
            <template #default="{ row }">
              <span class="mono">{{ row.target || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="错误" min-width="220">
            <template #default="{ row }">
              <span class="error-text">{{ row.last_error || '—' }}</span>
            </template>
          </el-table-column>
          <template #empty>
            <EmptyState
              title="暂无通知记录"
              description="触发离线 / 恢复告警或点击测试发送后，这里会显示发送记录。"
            />
          </template>
        </el-table>
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.channel-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: var(--vigil-space-4);
  align-items: start;
}

.channel-card {
  display: flex;
  flex-direction: column;
}

.channel-form :deep(.el-form-item) {
  margin-bottom: var(--vigil-space-3);
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 120px;
  gap: var(--vigil-space-3);
}

.full-width,
.port-input {
  width: 100%;
}

.field-hint {
  margin: var(--vigil-space-1) 0 0;
  font-size: var(--vigil-font-xs);
  color: var(--vigil-color-text-muted);
}

.channel-actions {
  display: flex;
  align-items: center;
  gap: var(--vigil-space-2);
  padding-top: var(--vigil-space-1);
}

.test-result {
  margin-top: var(--vigil-space-3);
}

.rate-limit {
  display: inline-flex;
  align-items: center;
  gap: var(--vigil-space-2);
  font-size: var(--vigil-font-xs);
}

.error-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--vigil-space-3);
}

.error-text {
  font-size: var(--vigil-font-xs);
  color: var(--vigil-color-critical);
  overflow-wrap: anywhere;
}

.table-error {
  margin: var(--vigil-space-3) var(--vigil-space-3) 0;
  width: auto;
}

@media (max-width: 640px) {
  .form-row {
    grid-template-columns: 1fr;
  }
}
</style>
