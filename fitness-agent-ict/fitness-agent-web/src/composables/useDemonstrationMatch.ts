import { onBeforeUnmount, shallowRef, watch } from 'vue'
import { findLocalDemonstration } from '../services/demonstrationService'
import type { DemonstrationMatchState, DemonstrationQuery } from '../types/demonstration'

export function useDemonstrationMatch(getQuery: () => DemonstrationQuery | null) {
  const state = shallowRef<DemonstrationMatchState>({ status: 'idle', match: null })
  let activeController: AbortController | null = null

  function stop() {
    activeController?.abort()
    activeController = null
  }

  async function reload() {
    stop()
    const query = getQuery()
    if (!query) {
      state.value = { status: 'idle', match: null }
      return
    }
    const controller = new AbortController()
    activeController = controller
    state.value = { status: 'loading', match: null }
    try {
      const match = await findLocalDemonstration(query, controller.signal)
      if (activeController !== controller) return
      state.value = match ? { status: 'matched', match } : { status: 'no_match', match: null }
    } catch {
      if (activeController !== controller || controller.signal.aborted) return
      state.value = { status: 'failed', match: null }
    } finally {
      if (activeController === controller) activeController = null
    }
  }

  watch(getQuery, reload, { immediate: true, flush: 'sync' })
  onBeforeUnmount(stop)
  return { state, reload }
}
