<script setup lang="ts">
import PlaceholderPage from '@/components/PlaceholderPage.vue'
</script>

<template>
  <PlaceholderPage
    title="模块管理"
    milestone="M4"
    description="内置模块清单、外部脚本清单与哈希校验、导入与白名单管理将在模块契约落地后接入。"
    :features="['内置模块', '外部脚本', 'SHA-256 校验', '依赖关系', '参数编辑']"
  />
</template>
