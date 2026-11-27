<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { EmptyState, TimeText, getErrorMessage, type PairingRequest } from '@vigil/shared'
import { ElMessage, ElMessageBox } from 'element-plus'
import { adminApi } from '@/api/admin'

const STATUS_META: Record<string, { label: string; type: string }> = {
  pending: { label: '待审批', type: 'warning' },
  approved: { label: '已批准', type: 'success' },
  claimed: { label: '已领取', type: 'success' },
  denied: { label: '已拒绝', type: 'danger' },
  expired: { label: '已过期', type: 'info' },
}

const items = ref<PairingRequest[]>([])
const loading = ref(false)
const error = ref('')
const filter = ref('pending')
const busyId = ref('')
const nowSeconds = ref(Math.floor(Date.now() / 1000))

const dialogVisible = ref(false)
const dialogItem = ref<PairingRequest | null>(null)
const code = ref('')
const approving = ref(false)

const pendingCount = computed(
  () => items.value.filter((item) => item.status === 'pending').length,
)

let refreshTimer: number | undefined
let clockTimer: number | undefined

function statusMeta(status: string) {
  return STATUS_META[status] ?? { label: status || '未知', type: 'info' }
}

function remainingSeconds(row: PairingRequest) {
  if (!row.code_expires_at) return 0
  return Math.max(0, Math.floor(Date.parse(row.code_expires_at) / 1000) - nowSeconds.value)
}

function remainingLabel(row: PairingRequest) {
  const seconds = remainingSeconds(row)
  if (seconds <= 0) return '已过期'
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

async function load(silent = false) {
  if (loading.value) return
  loading.value = true
  if (!silent) error.value = ''
  try {
    const data = await adminApi.pairings(filter.value)
    items.value = data.items ?? []
  } catch (err) {
    if (!silent) error.value = getErrorMessage(err, '配对请求加载失败')
  } finally {
    loading.value = false
  }
}

function openApprove(row: PairingRequest) {
  dialogItem.value = row
  code.value = ''
  dialogVisible.value = true
}

async function approve() {
  if (!dialogItem.value || approving.value) return
  const value = code.value.trim()
  if (!value) {
    ElMessage.warning('请输入客户端显示的配对码')
    return
  }
  approving.value = true
  try {
    await adminApi.approvePairing(dialogItem.value.request_id, value)
    ElMessage.success('已批准配对，客户端将自动领取凭证')
    dialogVisible.value = false
    await load(true)
  } catch (err) {
    ElMessage.error(getErrorMessage(err, '批准失败'))
  } finally {
    approving.value = false
  }
}

async function deny(row: PairingRequest) {
  if (busyId.value) return
  busyId.value = row.request_id
  try {
    const result = await ElMessageBox.prompt('拒绝后客户端需要重新发起配对', '拒绝配对', {
      confirmButtonText: '拒绝',
      cancelButtonText: '取消',
      inputPlaceholder: '原因（可选，会回传给客户端）',
    })
    await adminApi.denyPairing(row.request_id, result.value ?? '')
    ElMessage.info('已拒绝该配对请求')
    await load(true)
  } catch (err) {
    if (err !== 'cancel' && err !== 'close') {
      ElMessage.error(getErrorMessage(err, '拒绝失败'))
    }
  } finally {
    busyId.value = ''
  }
}

onMounted(() => {
  load()
  refreshTimer = window.setInterval(() => load(true), 5000)
  clockTimer = window.setInterval(() => {
    nowSeconds.value = Math.floor(Date.now() / 1000)
  }, 1000)
})

onUnmounted(() => {
  if (refreshTimer !== undefined) window.clearInterval(refreshTimer)
  if (clockTimer !== undefined) window.clearInterval(clockTimer)
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">配对审批</h2>
        <p class="page-sub">
          待审批 {{ pendingCount }} 条 · 输入客户端页面显示的配对码完成绑定，配对码不会在服务端保存明文
        </p>
      </div>
      <div class="head-actions">
        <el-select v-model="filter" size="small" style="width: 130px" @change="load()">
          <el-option label="待审批" value="pending" />
          <el-option label="全部状态" value="" />
        </el-select>
        <el-button size="small" :loading="loading" @click="load()">刷新</el-button>
      </div>
    </div>

    <el-alert v-if="error" class="error-box" type="error" show-icon :closable="false">
      <div class="alert-row">
        <span>{{ error }}</span>
        <el-button size="small" text type="primary" @click="load()">重试</el-button>
      </div>
    </el-alert>

    <div class="panel">
      <div class="panel-body panel-body--flush">
        <el-table v-loading="loading" :data="items" row-key="request_id">
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="statusMeta(row.status).type" effect="light">
                {{ statusMeta(row.status).label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="客户端" min-width="200">
            <template #default="{ row }">
              <div class="client-name">{{ row.name || '未命名设备' }}</div>
              <div class="client-meta mono">
                {{ row.model || '未知型号' }} · {{ row.fw_version || '未知固件' }}
              </div>
            </template>
          </el-table-column>
          <el-table-column label="来源 IP" width="140">
            <template #default="{ row }">
              <span class="mono">{{ row.client_ip || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="发起时间" width="170">
            <template #default="{ row }">
              <TimeText :value="row.created_at" mode="datetime" />
            </template>
          </el-table-column>
          <el-table-column label="配对码剩余" width="120">
            <template #default="{ row }">
              <span v-if="row.status === 'pending'" class="mono">
                {{ remainingLabel(row) }}
              </span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="尝试" width="70">
            <template #default="{ row }">
              <span class="mono">{{ row.attempts }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="170" fixed="right">
            <template #default="{ row }">
              <template v-if="row.status === 'pending'">
                <el-button size="small" type="primary" @click="openApprove(row)">批准</el-button>
                <el-button
                  size="small"
                  type="danger"
                  plain
                  :loading="busyId === row.request_id"
                  @click="deny(row)"
                >
                  拒绝
                </el-button>
              </template>
              <span v-else class="muted">{{ row.denied_reason || '已处理' }}</span>
            </template>
          </el-table-column>
          <template #empty>
            <EmptyState
              title="暂无待审批请求"
              description="客户端在设置页点击「立即注册」后，这里会出现对应的配对请求。"
            />
          </template>
        </el-table>
      </div>
    </div>

    <el-dialog v-model="dialogVisible" title="批准配对" width="420px">
      <p class="dialog-hint">
        请输入客户端页面显示的 8 位配对码。连续输错 5 次将作废本次请求。
      </p>
      <div v-if="dialogItem" class="dialog-target">
        <span class="mono">{{ dialogItem.name }}</span>
        <span class="muted">{{ dialogItem.client_ip }}</span>
      </div>
      <el-input
        v-model="code"
        class="code-input mono"
        maxlength="12"
        placeholder="例如 A1B2-C3D4"
        @keyup.enter="approve"
      />
      <template #footer>
        <el-button size="small" @click="dialogVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="approving" @click="approve">
          确认批准
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.head-actions {
  display: flex;
  align-items: center;
  gap: var(--vigil-space-2);
}

.alert-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--vigil-space-2);
}

.client-name {
  font-weight: 500;
}

.client-meta {
  margin-top: 2px;
  font-size: var(--vigil-font-xxs);
  color: var(--vigil-color-text-muted);
}

.dialog-hint {
  margin: 0 0 var(--vigil-space-2);
  color: var(--vigil-color-text-muted);
  font-size: var(--vigil-font-size-sm);
}

.dialog-target {
  display: flex;
  justify-content: space-between;
  gap: var(--vigil-space-2);
  margin-bottom: var(--vigil-space-2);
  font-size: var(--vigil-font-size-sm);
}

.code-input :deep(input) {
  font-size: 20px;
  letter-spacing: 3px;
  text-align: center;
}
</style>
