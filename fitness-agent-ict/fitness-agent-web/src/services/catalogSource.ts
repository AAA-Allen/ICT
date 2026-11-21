import { computed, ref, shallowRef } from 'vue'
import { muscles } from '../data/muscles'
import { exercises } from '../data/exercises'
import { demonstrations } from '../data/demonstrations'
import { parseJavaCatalog } from './catalogValidation'
import type { CatalogSource, QueryCatalog } from '../types/catalog'

const localCatalog: QueryCatalog = { muscles, exercises, demonstrations }
export const queryCatalog = shallowRef<QueryCatalog>(localCatalog)
export const catalogSource = ref<CatalogSource>('local')
export const catalogRevision = ref(0)
export const catalogLoading = ref(false)
export const catalogError = ref('')
export const catalogLoadedAt = ref<string | null>(null)
export const catalogSourceLabel = computed(() =>
  catalogSource.value === 'java' ? 'Java 资料快照' : '前端本地资料',
)
let activeController: AbortController | null = null
let watchdog: number | null = null

function stop() {
  const previous = activeController
  activeController = null
  previous?.abort()
  if (watchdog !== null) window.clearTimeout(watchdog)
  watchdog = null
  catalogLoading.value = false
}

export function useLocalCatalog() {
  stop()
  queryCatalog.value = localCatalog
  catalogSource.value = 'local'
  catalogRevision.value += 1
  catalogError.value = ''
  catalogLoadedAt.value = null
}

export async function loadJavaCatalog() {
  stop()
  const controller = new AbortController()
  activeController = controller
  catalogLoading.value = true
  catalogError.value = ''
  let timedOut = false
  watchdog = window.setTimeout(() => {
    timedOut = true
    controller.abort()
  }, 4000)
  try {
    const response = await fetch('/api/v1/catalog', {
      signal: controller.signal,
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    })
    if (!response.ok)
      throw new Error(`资料服务返回 HTTP ${response.status}，请检查 Java 终端是否正在运行。`)
    const catalog = parseJavaCatalog(await response.json())
    if (activeController !== controller) return
    queryCatalog.value = catalog
    catalogSource.value = 'java'
    catalogRevision.value += 1
    catalogLoadedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  } catch (error) {
    if (activeController !== controller) return
    catalogError.value = timedOut
      ? '等待 Java 资料超过 4 秒，请确认服务已启动后重试。'
      : error instanceof Error
        ? error.message
        : 'Java 资料读取失败，请重试。'
  } finally {
    if (activeController === controller) {
      if (watchdog !== null) window.clearTimeout(watchdog)
      watchdog = null
      activeController = null
      catalogLoading.value = false
    }
  }
}

export function cancelCatalogLoad() {
  stop()
}
