import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { cpSync, existsSync, mkdirSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const localPath = (path: string) => fileURLToPath(new URL(path, import.meta.url))

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const assets = localPath('./public/live-assets/')
  const model = localPath('../../squat-algorithm-handoff-20261119/models/pose_landmarker_lite.task')
  mkdirSync(assets, { recursive: true })
  cpSync(localPath('./node_modules/@mediapipe/tasks-vision/wasm/'), assets, { recursive: true })
  if (existsSync(model)) cpSync(model, localPath('./public/live-assets/pose_landmarker_lite.task'))
  const tls =
    env.LIVE_TLS_CERT && env.LIVE_TLS_KEY
      ? { cert: readFileSync(env.LIVE_TLS_CERT), key: readFileSync(env.LIVE_TLS_KEY) }
      : undefined
  const proxy = {
    '/api/v1': {
      target: env.VITE_QUERY_PROXY_TARGET || 'http://127.0.0.1:8081',
      changeOrigin: true,
    },
  }
  return {
    plugins: [vue()],
    server: {
      proxy,
      watch: {
        // 临时浏览器资料和 npm 缓存不属于源码，避免热更新扫描到被占用的文件。
        ignored: ['**/.qa/**', '**/.npm-cache/**'],
      },
    },
    preview: { proxy: tls ? {} : proxy, https: tls },
  }
})
