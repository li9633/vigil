<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { getErrorMessage } from '@vigil/shared'
import { clientApi } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const formRef = ref<FormInstance>()
const form = reactive({ token: '' })
const loading = ref(false)
const error = ref('')

const rules: FormRules = {
  token: [{ required: true, message: '请输入本地管理员令牌', trigger: 'blur' }],
}

async function submit() {
  if (loading.value) return
  error.value = ''
  const formEl = formRef.value
  if (!formEl) return
  const valid = await formEl.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    await clientApi.login({ token: form.token.trim() })
    auth.signIn()
    ElMessage.success('登录成功')
    const redirect = route.query.redirect
    const target =
      typeof redirect === 'string' && redirect.startsWith('/') && !redirect.startsWith('//')
        ? redirect
        : '/'
    await router.replace(target)
  } catch (err) {
    error.value = getErrorMessage(err, '登录失败，请检查令牌')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="brand-row">
        <span class="brand-mark" aria-hidden="true">V</span>
        <div class="brand-text">
          <strong>Vigil</strong>
          <small>路由器守护</small>
        </div>
      </div>

      <h1>本地管理员登录</h1>
      <p class="hint">请输入安装时生成的本地管理员令牌。连续失败会被限流。</p>

      <el-alert v-if="error" class="login-error" type="error" show-icon :closable="false" :title="error" />

      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="submit">
        <el-form-item label="管理员令牌" prop="token">
          <el-input
            v-model="form.token"
            size="large"
            type="password"
            show-password
            autocomplete="current-password"
            placeholder="安装时生成的令牌"
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-button class="submit-btn" type="primary" native-type="submit" :loading="loading">
          {{ loading ? '登录中…' : '登录' }}
        </el-button>
      </el-form>

      <p class="foot">仅限本机局域网访问，请勿将管理端口暴露到公网。</p>
    </div>
  </div>
</template>

<style scoped lang="scss">
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: var(--vigil-space-4);
  background: var(--vigil-glow-primary), var(--vigil-color-bg);
}

.login-card {
  width: 100%;
  max-width: 360px;
  padding: var(--vigil-space-7);
  background: var(--vigil-color-surface);
  border: 1px solid var(--vigil-color-border);
  border-radius: var(--vigil-radius-lg);
  box-shadow: var(--vigil-shadow-lg);

  h1 {
    margin: var(--vigil-space-4) 0 var(--vigil-space-1);
    font-size: var(--vigil-font-lg);
  }
}

.brand-row {
  display: flex;
  align-items: center;
  gap: var(--vigil-space-2);
}

.brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: var(--vigil-radius-sm);
  background: var(--vigil-gradient-brand);
  color: var(--vigil-color-on-primary);
  font-weight: 700;
  box-shadow: var(--vigil-shadow-glow);
}

.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.2;

  strong {
    font-size: var(--vigil-font-md);
    letter-spacing: 0.05em;
  }

  small {
    font-size: var(--vigil-font-xxs);
    color: var(--vigil-color-text-muted);
  }
}

.hint {
  margin: 0 0 var(--vigil-space-4);
  font-size: var(--vigil-font-xs);
  color: var(--vigil-color-text-muted);
  line-height: 1.6;
}

.login-error {
  margin-bottom: var(--vigil-space-3);
}

.submit-btn {
  width: 100%;
}

.foot {
  margin: var(--vigil-space-4) 0 0;
  font-size: var(--vigil-font-xxs);
  color: var(--vigil-color-text-muted);
  line-height: 1.5;
}
</style>
