import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import { configureApi } from '@vigil/shared'
import App from './App.vue'
import router from './router'
import './styles/app.scss'

configureApi({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '',
  credentials: 'include',
  unauthorizedRedirect: '/login',
})

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })
app.mount('#app')
