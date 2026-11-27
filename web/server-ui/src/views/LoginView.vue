<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { getErrorMessage } from '@vigil/shared'
import { adminApi } from '@/api/admin'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const formRef = ref<FormInstance>()
const form = reactive({ username: '', password: '' })
const loading = ref(false)
const error = ref('')

const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
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
    const response = await adminApi.login({
      username: form.username.trim(),
      password: form.password,
    })
    auth.signIn(response?.actor || response?.username || form.username.trim())
    ElMessage.success('登录成功')
    const redirect = route.query.redirect
    const target =
      typeof redirect === 'string' && redirect.startsWith('/') && !redirect.startsWith('//')
        ? redirect
        : '/'
    await router.replace(target)
  } catch (err) {
    error.value = getErrorMessage(err, '登录失败，请检查用户名与密码')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <section class="brand-panel">
      <div class="brand-mark" aria-hidden="true">V</div>
      <h1>Vigil</h1>
      <p class="tagline">自愈式互联网连接守护</p>
      <ul class="brand-points">
        <li>心跳与主机保活双通道判定</li>
        <li>分层检测与故障归因</li>
        <li>低风险自动恢复与告警通知</li>
      </ul>
      <p class="brand-foot">v1 · 服务端管理台</p>
    </section>

    <section class="form-panel">
      <div class="form-card">
        <h2>管理员登录</h2>
        <p class="form-sub">使用服务端管理员账号登录管理台</p>

        <el-alert
          v-if="error"
          class="login-error"
          :title="error"
          type="error"
          show-icon
          :closable="false"
        />

        <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="submit">
          <el-form-item label="用户名" prop="username">
            <el-input
              v-model="form.username"
              size="large"
              autocomplete="username"
              placeholder="admin"
            />
          </el-form-item>
          <el-form-item label="密码" prop="password">
            <el-input
              v-model="form.password"
              size="large"
              type="password"
              show-password
              autocomplete="current-password"
              placeholder="请输入密码"
              @keyup.enter="submit"
            />
          </el-form-item>
          <el-button class="submit-btn" type="primary" native-type="submit" :loading="loading">
            {{ loading ? '登录中…' : '登录' }}
          </el-button>
        </el-form>
      </div>
    </section>
  </div>
</template>

<style scoped lang="scss">
.login-page {
  display: flex;
  min-height: 100vh;
  background: var(--vigil-color-bg);
}

.brand-panel {
  position: relative;
  display: flex;
  flex: 0 0 55%;
  flex-direction: column;
  justify-content: center;
  overflow: hidden;
  padding: var(--vigil-space-9);
  background: var(--vigil-color-brand-panel);
  color: var(--vigil-color-brand-panel-text);

  &::before {
    content: '';
    position: absolute;
    inset: 0;
    background: var(--vigil-glow-primary), var(--vigil-glow-accent);
    pointer-events: none;
  }

  > * {
    position: relative;
  }

  h1 {
    margin: var(--vigil-space-4) 0 var(--vigil-space-1);
    font-size: var(--vigil-font-xxl);
    font-weight: 700;
    letter-spacing: -0.02em;
    color: transparent;
    background: var(--vigil-gradient-brand);
    -webkit-background-clip: text;
    background-clip: text;
  }
}

.brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  border-radius: var(--vigil-radius-md);
  background: var(--vigil-gradient-brand);
  color: var(--vigil-color-on-primary);
  font-size: 26px;
  font-weight: 700;
  box-shadow: var(--vigil-shadow-glow);
}

.tagline {
  margin: 0 0 var(--vigil-space-6);
  color: var(--vigil-color-brand-panel-muted);
  font-size: var(--vigil-font-md);
}

.brand-points {
  display: flex;
  flex-direction: column;
  gap: var(--vigil-space-2);
  margin: 0;
  padding: 0;
  list-style: none;
  color: var(--vigil-color-brand-panel-text);
  font-size: var(--vigil-font-sm);

  li::before {
    content: '—';
    margin-right: var(--vigil-space-2);
    color: var(--vigil-color-accent);
  }
}

.brand-foot {
  margin-top: var(--vigil-space-7);
  font-size: var(--vigil-font-xs);
  color: var(--vigil-color-brand-panel-muted);
}

.form-panel {
  display: flex;
  flex: 1;
  align-items: center;
  justify-content: center;
  padding: var(--vigil-space-6);
}

.form-card {
  width: 100%;
  max-width: 360px;
  padding: var(--vigil-space-7);
  background: var(--vigil-color-surface);
  border: 1px solid var(--vigil-color-border);
  border-radius: var(--vigil-radius-lg);
  box-shadow: var(--vigil-shadow-lg);

  h2 {
    font-size: var(--vigil-font-lg);
  }
}

.form-sub {
  margin: var(--vigil-space-1) 0 var(--vigil-space-5);
  font-size: var(--vigil-font-xs);
  color: var(--vigil-color-text-muted);
}

.login-error {
  margin-bottom: var(--vigil-space-4);
}

.submit-btn {
  width: 100%;
  margin-top: var(--vigil-space-2);
}

@media (max-width: 900px) {
  .brand-panel {
    display: none;
  }

  .form-panel {
    padding: var(--vigil-space-4);
    background: var(--vigil-glow-primary);
  }
}
</style>
