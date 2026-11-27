<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  EmptyState,
  LiveBadge,
  LoadingBlock,
  RefreshFab,
  StatusTag,
  TimeText,
  getErrorMessage,
  normalizeStatus,
  unwrapList,
  usePolling,
  type ClientEvent,
} from '@vigil/shared'
import { clientApi } from '@/api/client'

const events = ref<ClientEvent[]>([])
const loading = ref(false)
const error = ref('')
const levelFilter = ref('')

const LEVEL_OPTIONS = [
  { label: '全部级别', value: '' },
  { label: '正常', value: 'ok' },
  { label: '警告', value: 'warn' },
  { label: '严重', value: 'critical' },
  { label: '未知', value: 'unknown' },
]

const filtered = computed(() => {
  const list = [...events.value].sort(
    (a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime(),
  )
  if (!levelFilter.value) return list
  return list.filter((event) => normalizeStatus(event.level ?? event.type) === levelFilter.value)
})

async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    events.value = unwrapList(await clientApi.events(100))
  } catch (err) {
    error.value = getErrorMessage(err, '事件列表加载失败')
  } finally {
    loading.value = false
  }
}

const poll = usePolling(load, {
  interval: 10000,
  isError: () => Boolean(error.value),
})
</script>

<template>
  <div class="page pb-fab">
    <div class="page-head">
      <div>
        <h2 class="page-title">事件时间线</h2>
        <p class="page-sub">本地 incident、动作与上报状态，显示 {{ filtered.length }} 条</p>
      </div>
      <LiveBadge :at="poll.lastUpdated" :paused="poll.paused" />
    </div>

    <div class="panel">
      <div class="panel-head">
        <el-select v-model="levelFilter" size="small" class="level-select">
          <el-option
            v-for="option in LEVEL_OPTIONS"
            :key="option.value"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
        <span class="muted">{{ events.length }} 条记录</span>
      </div>

      <el-alert v-if="error" class="list-error" type="error" show-icon :closable="false">
        <div class="alert-row">
          <span>{{ error }}</span>
          <el-button size="small" text type="primary" @click="poll.retry">重试</el-button>
        </div>
      </el-alert>

      <LoadingBlock v-if="loading && !events.length" min-height="180px" text="正在读取事件…" />

      <EmptyState
        v-else-if="!filtered.length"
        compact
        title="暂无事件"
        description="当前筛选条件下没有事件记录。"
      />

      <ul v-else class="list">
        <li v-for="event in filtered" :key="event.event_id" class="list-item">
          <div class="list-main">
            <p class="list-title">{{ event.message || event.type }}</p>
            <p class="list-meta">
              {{ event.module || 'system' }} · {{ event.type }} ·
              <TimeText :value="event.ts" />
            </p>
          </div>
          <div class="list-side">
            <StatusTag :status="event.level || event.type" size="sm" />
            <StatusTag
              :status="event.uploaded === false ? 'WARN' : 'OK'"
              :label="event.uploaded === false ? '待上报' : '已上报'"
              size="sm"
            />
          </div>
        </li>
      </ul>
    </div>

    <RefreshFab
      :loading="poll.refreshing"
      :paused="poll.paused"
      :error="Boolean(error)"
      @refresh="poll.refresh"
    />
  </div>
</template>

<style scoped lang="scss">
.level-select {
  width: 130px;
}

.alert-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--vigil-space-2);
}

.list-error {
  margin: var(--vigil-space-2);
}
</style>
