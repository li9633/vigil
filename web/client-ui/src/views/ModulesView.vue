<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  EmptyState,
  LoadingBlock,
  StatusTag,
  getErrorMessage,
  type ModulesResponse,
  type ProbeInfo,
  type ScriptProbeInfo,
} from '@vigil/shared'
import { ElMessage, ElMessageBox } from 'element-plus'
import { clientApi } from '@/api/client'

const data = ref<ModulesResponse | null>(null)
const loading = ref(false)
const error = ref('')
const dialogVisible = ref(false)
const saving = ref(false)
const deleting = ref('')

const form = reactive({
  name: '',
  timeout: 10,
  content:
    '#!/bin/sh\n# 退出码：0=OK 1=WARN 2=CRITICAL 3=UNKNOWN\necho \'{"evidence":{"ok":true},"message":"ok"}\'\nexit 0\n',
})

const builtin = computed<ProbeInfo[]>(() => data.value?.builtin ?? [])
const inventory = computed(() => data.value?.scripts)
const scripts = computed<ScriptProbeInfo[]>(() => inventory.value?.scripts ?? [])
const invalidCount = computed(() => scripts.value.filter((item) => !item.valid).length)

function paramsText(probe: ProbeInfo): string {
  return probe.params?.length ? probe.params.join(' / ') : '无参数'
}

function hashText(value?: string | null): string {
  if (!value) return '—'
  return value.length > 12 ? `${value.slice(0, 12)}…` : value
}

function scriptState(script: ScriptProbeInfo): string {
  return script.valid ? 'OK' : 'CRITICAL'
}

function scriptLabel(script: ScriptProbeInfo): string {
  return script.valid ? '校验通过' : '校验失败'
}

async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    data.value = await clientApi.modules()
  } catch (err) {
    error.value = getErrorMessage(err, '模块列表加载失败')
  } finally {
    loading.value = false
  }
}

function openImport() {
  form.name = ''
  form.timeout = 10
  form.content =
    '#!/bin/sh\n# 退出码：0=OK 1=WARN 2=CRITICAL 3=UNKNOWN\necho \'{"evidence":{"ok":true},"message":"ok"}\'\nexit 0\n'
  dialogVisible.value = true
}

async function submitImport() {
  if (saving.value) return
  if (!/^[A-Za-z0-9_-]{1,32}$/.test(form.name.trim())) {
    ElMessage.warning('名称只能包含字母、数字、下划线和短横线，长度 1-32')
    return
  }
  if (!form.content.trim()) {
    ElMessage.warning('脚本内容不能为空')
    return
  }
  saving.value = true
  try {
    const result = await clientApi.importScript({
      name: form.name.trim(),
      content: form.content,
      timeout: form.timeout,
    })
    data.value = result.modules
    dialogVisible.value = false
    ElMessage.success(`脚本 ${result.script.name} 已导入并完成校验`)
  } catch (err) {
    ElMessage.error(getErrorMessage(err, '脚本导入失败'))
  } finally {
    saving.value = false
  }
}

async function remove(script: ScriptProbeInfo) {
  try {
    await ElMessageBox.confirm(
      `确定删除脚本探测器「${script.name}」？脚本文件与清单条目都会被移除。`,
      '删除脚本探测器',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  deleting.value = script.name
  try {
    const result = await clientApi.deleteScript(script.name)
    data.value = result.modules
    ElMessage.success('已删除')
  } catch (err) {
    ElMessage.error(getErrorMessage(err, '删除失败'))
  } finally {
    deleting.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">模块管理</h2>
        <p class="page-sub">
          内置探测器 {{ builtin.length }} 个 · 脚本 {{ scripts.length }} 个
          <template v-if="invalidCount">
            · 校验失败 <span class="text-critical">{{ invalidCount }}</span> 个
          </template>
        </p>
      </div>
      <div class="head-actions">
        <el-button size="small" :loading="loading" @click="load">刷新</el-button>
        <el-button size="small" type="primary" @click="openImport">导入脚本</el-button>
      </div>
    </div>

    <el-alert v-if="error" class="error-box" type="error" show-icon :closable="false">
      <div class="alert-row">
        <span>{{ error }}</span>
        <el-button size="small" text type="primary" @click="load">重试</el-button>
      </div>
    </el-alert>

    <LoadingBlock v-if="loading && !data" min-height="180px" text="正在读取模块信息…" />

    <template v-else-if="data">
      <div class="panel">
        <div class="panel-head">
          <div>
            <h3 class="panel-title">内置探测器</h3>
            <p class="panel-sub">由 agent 实现，Guard 通过 <span class="mono">probe</span> 字段引用</p>
          </div>
        </div>
        <div class="panel-body panel-body--flush">
          <ul class="list">
            <li v-for="probe in builtin" :key="probe.id" class="list-item">
              <div class="list-main">
                <p class="list-title">
                  {{ probe.name || probe.id }}
                  <span class="mono id-tag">{{ probe.id }}</span>
                </p>
                <p class="list-meta">{{ probe.description || '暂无说明' }}</p>
              </div>
              <div class="list-side">
                <span class="params">{{ paramsText(probe) }}</span>
              </div>
            </li>
          </ul>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <div>
            <h3 class="panel-title">脚本探测器</h3>
            <p class="panel-sub">
              目录 <span class="mono">{{ inventory?.directory }}</span>
              · 清单 <span class="mono">{{ inventory?.manifest }}</span>
            </p>
          </div>
        </div>

        <div class="panel-body panel-body--flush">
          <el-alert
            v-if="inventory?.error"
            class="inline-alert"
            type="warning"
            show-icon
            :closable="false"
            :title="inventory.error"
          />

          <ul v-if="scripts.length" class="list">
            <li v-for="script in scripts" :key="script.name" class="list-item">
              <div class="list-main">
                <p class="list-title">
                  {{ script.name }}
                  <span class="mono id-tag">{{ script.file }}</span>
                </p>
                <p class="list-meta">
                  超时 {{ script.timeout ?? '—' }}s · SHA-256
                  <span class="mono" :title="script.sha256 || ''">{{ hashText(script.sha256) }}</span>
                </p>
                <p v-if="script.error" class="list-meta text-critical">{{ script.error }}</p>
              </div>
              <div class="list-side">
                <StatusTag :status="scriptState(script)" :label="scriptLabel(script)" size="sm" />
                <el-button
                  size="small"
                  text
                  type="danger"
                  :loading="deleting === script.name"
                  @click="remove(script)"
                >
                  删除
                </el-button>
              </div>
            </li>
          </ul>

          <EmptyState
            v-else-if="!inventory?.error"
            title="暂无脚本探测器"
            description="导入脚本后会写入目录并自动计算 SHA-256，只有校验通过的脚本才会被执行。"
          >
            <template #action>
              <el-button size="small" type="primary" @click="openImport">导入脚本</el-button>
            </template>
          </EmptyState>

          <el-alert
            v-if="inventory?.unregistered?.length"
            class="inline-alert"
            type="info"
            show-icon
            :closable="false"
            title="目录中存在未登记的脚本文件"
          >
            <span class="mono">{{ inventory.unregistered.join('、') }}</span>
          </el-alert>
        </div>
      </div>
    </template>

    <el-dialog
      v-model="dialogVisible"
      title="导入脚本探测器"
      width="640px"
      :close-on-click-modal="false"
    >
      <el-form label-position="top" class="import-form">
        <div class="form-row">
          <el-form-item label="名称" class="grow">
            <el-input v-model="form.name" maxlength="32" placeholder="例如 openclash" />
          </el-form-item>
          <el-form-item label="超时（秒）">
            <el-input-number v-model="form.timeout" :min="1" :max="60" />
          </el-form-item>
        </div>
        <el-form-item label="脚本内容">
          <el-input
            v-model="form.content"
            type="textarea"
            :rows="10"
            class="mono-input"
            spellcheck="false"
          />
        </el-form-item>
      </el-form>
      <el-alert type="info" :closable="false" show-icon>
        脚本写入 <span class="mono">/etc/vigil/probes.d</span> 后自动计算 SHA-256；退出码
        0/1/2/3 对应 ok/warn/critical/unknown，stdout 必须为单行 JSON。
      </el-alert>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitImport">导入并校验</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.head-actions {
  display: flex;
  align-items: center;
  gap: var(--vigil-space-2);
}

.alert-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--vigil-space-2);
}

.id-tag {
  margin-left: var(--vigil-space-1);
  padding: 0 4px;
  border: 1px solid var(--vigil-color-border);
  border-radius: var(--vigil-radius-sm);
  color: var(--vigil-color-text-muted);
  font-weight: 400;
}

.params {
  color: var(--vigil-color-text-muted);
  font-size: var(--vigil-font-size-sm);
}

.inline-alert {
  margin: var(--vigil-space-2) var(--vigil-space-3);
}

.form-row {
  display: flex;
  gap: var(--vigil-space-3);

  .grow {
    flex: 1;
  }
}

.mono-input :deep(textarea) {
  font-family: var(--vigil-font-mono);
}
</style>
