<script setup lang="ts">
import MilestonePlaceholder from '@/components/MilestonePlaceholder.vue'
</script>

<template>
  <MilestonePlaceholder
    title="审计日志"
    milestone="M3"
    description="登录、配置变更与手动操作审计将在审计日志接口完成后接入，保留 180 天。"
    :features="['登录记录', '配置变更', '手动操作', 'secret 轮换', '时间范围筛选', '导出']"
  />
</template>
