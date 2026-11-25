<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { EmptyState, StatusTag, TimeText, getErrorMessage, unwrapList, type Client } from '@vigil/shared'
import { adminApi } from '@/api/admin'

const router = useRouter()

const clients = ref<Client[]>([])
const loading = ref(false)
const error = ref('')
const keyword = ref('')
const stateFilter = ref('')

const STATE_OPTIONS = [
  'HEALTHY',
  'SUSPECT',
  'DOWN',
  'AGENT_DOWN',
  'HOST_OR_NET_DOWN',
  'BEACON_DOWN',
  'DEVICE_REBOOT',
  'FLAPPING',
  'UNKNOWN',
]

const filtered = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  return clients.value.filter((client) => {
    const haystack = [client.name, client.client_id, client.wan_ip, client.agent_version, client.model]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
    const matchKeyword = !text || haystack.includes(text)
    const matchState = !stateFilter.value || (client.state ?? 'UNKNOWN') === stateFilter.value
    return matchKeyword && matchState
  })
})

async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    clients.value = unwrapList(await adminApi.clients())
  } catch (err) {
    error.value = getErrorMessage(err, '客户端列表加载失败')
  } finally {
    loading.value = false
  }
}

function openClient(row: Client) {
  router.push({ name: 'client-detail', params: { id: row.client_id } })
}

function resetFilters() {
  keyword.value = ''
  stateFilter.value = ''
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">客户端</h2>
        <p class="page-sub">共 {{ clients.length }} 台设备，当前筛选 {{ filtered.length }} 台</p>
      </div>
      <el-button size="small" :loading="loading" @click="load">刷新</el-button>
    </div>

    <div class="panel">
      <div class="panel-head">
        <div class="toolbar">
          <el-input
            v-model="keyword"
            size="small"
            clearable
            placeholder="搜索名称 / ID / WAN IP"
            class="search-input"
          />
          <el-select v-model="stateFilter" size="small" clearable placeholder="全部状态" class="state-select">
            <el-option v-for="state in STATE_OPTIONS" :key="state" :label="state" :value="state" />
          </el-select>
          <el-button size="small" text @click="resetFilters">重置</el-button>
        </div>
      </div>

      <el-alert
        v-if="error"
        class="table-error"
        type="error"
        show-icon
        :closable="false"
      >
        <span>{{ error }}</span>
        <el-button size="small" text type="primary" @click="load">重试</el-button>
      </el-alert>

      <div class="panel-body panel-body--flush">
        <el-table
          v-loading="loading"
          :data="filtered"
          row-key="client_id"
          class="clickable-table"
          @row-click="openClient"
        >
          <el-table-column label="客户端" min-width="190">
            <template #default="{ row }">
              <div class="client-name">{{ row.name || row.client_id }}</div>
              <div class="mono muted">{{ row.client_id }}</div>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="120">
            <template #default="{ row }">
              <StatusTag :status="row.state" />
            </template>
          </el-table-column>
          <el-table-column label="最后心跳" width="130">
            <template #default="{ row }">
              <TimeText :value="row.last_seen" />
            </template>
          </el-table-column>
          <el-table-column label="Agent" width="96">
            <template #default="{ row }">
              <span class="mono">{{ row.agent_version || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="WAN IP" min-width="130">
            <template #default="{ row }">
              <span class="mono">{{ row.wan_ip || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="最近故障" min-width="220">
            <template #default="{ row }">
              <div v-if="row.last_incident" class="incident-cell">
                <StatusTag :status="row.last_incident.state" size="sm" />
                <span class="incident-text">{{ row.last_incident.summary || row.last_incident.category }}</span>
              </div>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="" width="76" align="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click.stop="openClient(row)">详情</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <EmptyState
              title="暂无客户端"
              description="等待 agent 注册并上报心跳后，这里会显示客户端列表。"
            />
          </template>
        </el-table>
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.search-input {
  width: 220px;
}

.state-select {
  width: 150px;
}

.client-name {
  font-size: var(--vigil-font-sm);
  font-weight: 600;
}

.incident-cell {
  display: flex;
  align-items: center;
  gap: var(--vigil-space-2);
  min-width: 0;
}

.incident-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--vigil-font-xs);
  color: var(--vigil-color-text-secondary);
}

.table-error {
  margin: var(--vigil-space-3) var(--vigil-space-3) 0;
  width: auto;
}

.clickable-table :deep(.el-table__row) {
  cursor: pointer;
}

@media (max-width: 640px) {
  .search-input,
  .state-select {
    width: 100%;
  }
}
</style>
