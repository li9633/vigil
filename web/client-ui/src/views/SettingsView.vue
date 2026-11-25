<script setup lang="ts">
import PlaceholderPage from '@/components/PlaceholderPage.vue'
</script>

<template>
  <PlaceholderPage
    title="设置"
    milestone="M4"
    description="服务端地址、心跳间隔、探测目标与维护窗口配置将在 agent 设置接口完成后接入。"
    :features="['服务端地址', '心跳间隔', '探测目标', '维护窗口', '版本信息']"
  />
</template>
