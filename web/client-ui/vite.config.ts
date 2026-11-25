import path from 'node:path'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

const appSrc = fileURLToPath(new URL('./src', import.meta.url))
const sharedSrc = fileURLToPath(new URL('../shared/src', import.meta.url))

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // 本地开发默认代理到路由器上的 agent HTTP 服务；可用 VITE_API_PROXY 覆盖
  const proxyTarget = env.VITE_API_PROXY || 'http://192.168.1.1:8787'

  return {
    plugins: [vue()],
    resolve: {
      alias: [
        { find: /^@\//, replacement: `${appSrc}/` },
        { find: /^@vigil\/shared$/, replacement: path.join(sharedSrc, 'index.ts') },
        { find: /^@vigil\/shared\//, replacement: `${sharedSrc}/` },
      ],
    },
    css: {
      preprocessorOptions: {
        scss: {
          // Vite 5 默认使用 legacy Sass API（includePaths），同时兼容 modern API（loadPaths）
          loadPaths: [path.join(sharedSrc, 'styles')],
          includePaths: [path.join(sharedSrc, 'styles')],
        },
      },
    },
    server: {
      host: '127.0.0.1',
      port: 5174,
      proxy: {
        '/api': {
          target: proxyTarget,
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: 'dist',
      chunkSizeWarningLimit: 1500,
    },
  }
})
