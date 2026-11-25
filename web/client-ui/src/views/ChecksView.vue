<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  EmptyState,
  LoadingBlock,
  StatusTag,
  TimeText,
  formatLatency,
  getErrorMessage,
  unwrapList,
  type CheckItem,
} from '@vigil/shared'
import { clientApi } from '@/api/client'

const checks = ref<CheckItem[]>([])
const loading = ref(false)
const error = ref('')

const sorted = computed(() =>
  [...checks.value].sort((a, b) => {
    const left = `${a.layer ?? ''}${a.id}`
    const right = `${b.layer ?? ''}${b.id}`
    return left.localeCompare(right)
  }),
)

const abnormalCount = computed(
  () => checks.value.filter((item) => item.status !== 'ok').length,
)

function statusOf(check: CheckItem): string {
  return check.enabled === false ? 'DISABLED' : check.status
}

async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    checks.value = unwrapList(await clientApi.checks())
  } catch (err) {
    error.value = getErrorMessage(err, '检测项加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">检测项</h2>
        <p class="page-sub">
          共 {{ checks.length }} 项 · 异常 <span :class="{ 'text-critical': abnormalCount > 0 }">{{ abnormalCount }}</span> 项
        </p>
      </div>
      <el-button size="small" :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-alert v-if="error" class="error-box" type="error" show-icon :closable="false">
      <div class="alert-row">
        <span>{{ error }}</span>
        <el-button size="small" text type="primary" @click="load">重试</el-button>
      </div>
    </el-alert>

    <LoadingBlock v-if="loading && !checks.length" min-height="180px" text="正在读取检测结果…" />

    <EmptyState
      v-else-if="!checks.length && !loading"
      title="暂无检测项"
      description="分层检测模块尚未产生结果，或接口暂无返回。"
    >
      <template #action>
        <el-button size="small" @click="load">重新加载</el-button>
      </template>
    </EmptyState>

    <div v-else class="panel">
      <div class="panel-body panel-body--flush">
        <ul class="list">
          <li v-for="check in sorted" :key="check.id" class="list-item">
            <div class="list-main">
              <p class="list-title">
                {{ check.name || check.id }}
                <span class="mono layer-tag">{{ check.layer || '—' }}</span>
              </p>
              <p class="list-meta">{{ check.evidence || '暂无证据' }}</p>
              <p class="list-meta">
                间隔 {{ check.interval ?? '—' }}s
                <template v-if="check.updated_at">
                  · 更新于 <TimeText :value="check.updated_at" />
                </template>
              </p>
            </div>
            <div class="list-side">
              <StatusTag :status="statusOf(check)" size="sm" />
              <span>{{ formatLatency(check.latency_ms) }}</span>
            </div>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.alert-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--vigil-space-2);
}

.layer-tag {
  margin-left: var(--vigil-space-1);
  padding: 0 4px;
  border: 1px solid var(--vigil-color-border);
  border-radius: var(--vigil-radius-sm);
  color: var(--vigil-color-text-muted);
  font-weight: 400;
}
</style>
