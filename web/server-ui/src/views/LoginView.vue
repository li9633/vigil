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
        <li>心跳 / beacon 双通道判定</li>
        <li>分层检测与故障归因</li>
        <li>L1 自动恢复 + 通知</li>
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
            <el-input v-model="form.username" autocomplete="username" placeholder="admin" />
          </el-form-item>
          <el-form-item label="密码" prop="password">
            <el-input
              v-model="form.password"
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

        <p class="form-tip">会话由后端 HttpOnly Cookie 维持，前端不保存密码。</p>
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
  display: flex;
  flex-direction: column;
  justify-content: center;
  flex: 0 0 44%;
  padding: var(--vigil-space-9);
  color: #fff;
  background:
    radial-gradient(circle at 20% 15%, rgba(56, 189, 248, 0.28), transparent 42%),
    linear-gradient(155deg, #0b1626 0%, #111c2e 55%, #172a44 100%);

  h1 {
    margin: var(--vigil-space-4) 0 var(--vigil-space-1);
    font-size: var(--vigil-font-xxl);
    letter-spacing: 0.08em;
  }
}

.brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  border-radius: var(--vigil-radius-md);
  background: linear-gradient(135deg, #2563eb, #38bdf8);
  font-size: 26px;
  font-weight: 700;
}

.tagline {
  margin: 0 0 var(--vigil-space-6);
  color: rgba(255, 255, 255, 0.72);
  font-size: var(--vigil-font-md);
}

.brand-points {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--vigil-space-2);
  color: rgba(255, 255, 255, 0.82);
  font-size: var(--vigil-font-sm);

  li::before {
    content: '—';
    margin-right: var(--vigil-space-2);
    color: #60a5fa;
  }
}

.brand-foot {
  margin-top: var(--vigil-space-7);
  font-size: var(--vigil-font-xs);
  color: rgba(255, 255, 255, 0.4);
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
  max-width: 380px;
  padding: var(--vigil-space-6);
  background: var(--vigil-color-surface);
  border: 1px solid var(--vigil-color-border);
  border-radius: var(--vigil-radius-lg);
  box-shadow: var(--vigil-shadow-md);

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

.form-tip {
  margin: var(--vigil-space-4) 0 0;
  font-size: 11px;
  color: var(--vigil-color-text-muted);
  text-align: center;
}

@media (max-width: 860px) {
  .brand-panel {
    display: none;
  }

  .form-panel {
    padding: var(--vigil-space-4);
  }
}
</style>
