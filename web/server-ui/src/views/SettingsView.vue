<script setup lang="ts">
import MilestonePlaceholder from '@/components/MilestonePlaceholder.vue'
</script>

<template>
  <MilestonePlaceholder
    title="策略配置"
    milestone="M3"
    description="心跳阈值、抖动窗口、动作冷却与抑制规则的服务端配置页将在服务端配置接口完成后接入。"
    :features="['SUSPECT 90s / DOWN 150s', '抖动窗口 30 分钟 / 3 次', '动作冷却 5 分钟', '通知限流', '数据保留策略']"
  />
</template>
