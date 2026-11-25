<script setup lang="ts">
import { EmptyState } from '@vigil/shared'

withDefaults(
  defineProps<{
    title: string
    milestone: string
    description?: string
    features?: string[]
  }>(),
  { description: '', features: () => [] },
)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">{{ title }}</h2>
        <p class="page-sub">占位页面 · 计划在 {{ milestone }} 里程碑实现</p>
      </div>
      <el-tag type="info" effect="plain">{{ milestone }} 待实现</el-tag>
    </div>

    <div class="panel">
      <div class="panel-body">
        <EmptyState :title="`${title}尚未实现`" :description="description || '当前为工程骨架，仅提供路由与占位视图。'">
          <template #action>
            <div v-if="features.length" class="feature-list">
              <span v-for="feature in features" :key="feature" class="feature-item">{{ feature }}</span>
            </div>
          </template>
        </EmptyState>
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.feature-list {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: var(--vigil-space-2);
  max-width: 560px;
}

.feature-item {
  padding: 2px 10px;
  border: 1px solid var(--vigil-color-border);
  border-radius: var(--vigil-radius-pill);
  background: var(--vigil-color-surface-alt);
  color: var(--vigil-color-text-secondary);
  font-size: var(--vigil-font-xs);
}
</style>
