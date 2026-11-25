<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  EmptyState,
  StatusTag,
  TimeText,
  getErrorMessage,
  unwrapList,
  type Incident,
} from '@vigil/shared'
import { adminApi } from '@/api/admin'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

const incidents = ref<Incident[]>([])
const loading = ref(false)
const error = ref('')
const filters = reactive({ state: '', category: '', clientId: '' })
const acking = ref<Record<string, boolean>>({})

const STATE_OPTIONS = [
  { label: '全部状态', value: '' },
  { label: '疑似异常', value: 'SUSPECT' },
  { label: '已中断', value: 'DOWN' },
  { label: '已恢复', value: 'RECOVERED' },
]

const activeFilterCount = computed(
  () => Number(Boolean(filters.state)) + Number(Boolean(filters.category.trim())) + Number(Boolean(filters.clientId.trim())),
)

function canAck(incident: Incident): boolean {
  return !incident.acked_at && incident.state !== 'RECOVERED' && incident.state !== 'CLOSED'
}

async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    incidents.value = unwrapList(
      await adminApi.incidents({
        state: filters.state || undefined,
        category: filters.category.trim() || undefined,
        client_id: filters.clientId.trim() || undefined,
        limit: 200,
      }),
    )
  } catch (err) {
    error.value = getErrorMessage(err, '告警列表加载失败')
  } finally {
    loading.value = false
  }
}

async function resetFilters() {
  filters.state = ''
  filters.category = ''
  filters.clientId = ''
  await load()
}

async function acknowledge(incident: Incident) {
  const key = String(incident.id)
  if (acking.value[key]) return

  try {
    await ElMessageBox.confirm(
      `确认告警「${incident.summary || incident.category}」？确认后仅表示已知悉，不会停止状态机判定。`,
      '确认告警',
      { type: 'warning', confirmButtonText: '确认', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  acking.value = { ...acking.value, [key]: true }
  try {
    const updated = await adminApi.ackIncident(key)
    incidents.value = incidents.value.map((item) => {
      if (String(item.id) !== key) return item
      if (updated && typeof updated === 'object' && 'id' in updated) {
        return { ...item, ...updated }
      }
      return {
        ...item,
        state: 'ACKED',
        acked_at: new Date().toISOString(),
        acked_by: auth.username || 'admin',
      }
    })
    ElMessage.success('告警已确认')
  } catch (err) {
    ElMessage.error(getErrorMessage(err, '确认失败，请稍后重试'))
  } finally {
    const next = { ...acking.value }
    delete next[key]
    acking.value = next
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">告警中心</h2>
        <p class="page-sub">按客户端 / 类别 / 时间聚合，支持确认操作{{ activeFilterCount ? ` · 已启用 ${activeFilterCount} 个筛选` : '' }}</p>
      </div>
      <div class="toolbar">
        <el-button size="small" :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head filters">
        <el-select v-model="filters.state" size="small" class="filter-select" @change="load">
          <el-option v-for="option in STATE_OPTIONS" :key="option.value" :label="option.label" :value="option.value" />
        </el-select>
        <el-input
          v-model="filters.category"
          size="small"
          clearable
          class="filter-input"
          placeholder="类别，如 pppoe / dns"
          @keyup.enter="load"
          @clear="load"
        />
        <el-input
          v-model="filters.clientId"
          size="small"
          clearable
          class="filter-input"
          placeholder="client_id"
          @keyup.enter="load"
          @clear="load"
        />
        <el-button size="small" @click="load">查询</el-button>
        <el-button size="small" text @click="resetFilters">重置</el-button>
      </div>

      <el-alert v-if="error" class="table-error" type="error" show-icon :closable="false">
        <div class="error-actions">
          <span>{{ error }}</span>
          <el-button size="small" text type="primary" @click="load">重试</el-button>
        </div>
      </el-alert>

      <div class="panel-body panel-body--flush">
        <el-table v-loading="loading" :data="incidents" row-key="id">
          <el-table-column label="开始时间" width="150">
            <template #default="{ row }">
              <TimeText :value="row.started_at" mode="datetime" />
            </template>
          </el-table-column>
          <el-table-column label="客户端" min-width="150">
            <template #default="{ row }">
              <div class="cell-strong">{{ row.client_name || row.client_id }}</div>
              <div class="mono muted">{{ row.client_id }}</div>
            </template>
          </el-table-column>
          <el-table-column label="类别" width="100">
            <template #default="{ row }">
              <span class="mono">{{ row.category }}</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <StatusTag :status="row.state" size="sm" />
            </template>
          </el-table-column>
          <el-table-column label="摘要" min-width="220">
            <template #default="{ row }">
              <span class="summary">
                <template v-if="row.guard_name">[{{ row.guard_name }}] </template>{{ row.summary || '—' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="恢复时间" width="150">
            <template #default="{ row }">
              <TimeText :value="row.recovered_at" mode="datetime" />
            </template>
          </el-table-column>
          <el-table-column label="确认" width="150">
            <template #default="{ row }">
              <div v-if="row.acked_at" class="ack-info">
                <span class="ack-text">{{ row.acked_by || '管理员' }}</span>
                <TimeText :value="row.acked_at" mode="datetime" />
              </div>
              <span v-else class="muted">未确认</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="96" align="right">
            <template #default="{ row }">
              <el-button
                v-if="canAck(row)"
                link
                type="primary"
                size="small"
                :loading="Boolean(acking[String(row.id)])"
                @click="acknowledge(row)"
              >
                确认
              </el-button>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <template #empty>
            <EmptyState
              title="没有匹配的告警"
              description="当前筛选条件下没有记录；调整筛选或等待状态机产生新告警。"
            />
          </template>
        </el-table>
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.filters {
  justify-content: flex-start;
}

.filter-select {
  width: 130px;
}

.filter-input {
  width: 190px;
}

.cell-strong {
  font-size: var(--vigil-font-sm);
  font-weight: 600;
}

.summary {
  font-size: var(--vigil-font-xs);
  color: var(--vigil-color-text-secondary);
  overflow-wrap: anywhere;
}

.ack-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: var(--vigil-font-xs);
  color: var(--vigil-color-text-muted);
}

.ack-text {
  color: var(--vigil-color-text-secondary);
}

.table-error {
  margin: var(--vigil-space-3) var(--vigil-space-3) 0;
  width: auto;
}

.error-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--vigil-space-3);
}

@media (max-width: 720px) {
  .filter-select,
  .filter-input {
    width: 100%;
  }
}
</style>
