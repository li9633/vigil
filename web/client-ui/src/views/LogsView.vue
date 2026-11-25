<script setup lang="ts">
import PlaceholderPage from '@/components/PlaceholderPage.vue'
</script>

<template>
  <PlaceholderPage
    title="日志"
    milestone="M4"
    description="按级别 / 模块过滤日志与导出脱敏证据包将在 agent 日志接口完成后接入。"
    :features="['级别过滤', '模块过滤', '实时查看', '脱敏导出']"
  />
</template>
