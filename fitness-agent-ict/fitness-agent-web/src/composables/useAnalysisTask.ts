import { computed, onBeforeUnmount, shallowRef, watch } from 'vue'
import { MOCK_ANALYSIS_TIMEOUT_MS } from '../data/analysisScenarios'
import { runMockAnalysisTask } from '../services/mockAnalysisService'
import type {
  AnalysisInput,
  AnalysisTaskState,
  MockAnalysisScenario,
  MockAnalysisTask,
} from '../types/analysis'
import type { MockResultPreset } from '../types/analysisResult'

interface ActiveRun {
  task: MockAnalysisTask
  controller: AbortController
  watchdog: number
}

let nextTaskNumber = 0

export function useAnalysisTask(getInput: () => AnalysisInput | null) {
  const state = shallowRef<AnalysisTaskState>({
    status: 'idle',
    task: null,
    message: '选择模拟场景后开始，每次只运行一个本地任务。',
    result: null,
  })
  const isBusy = computed(
    () => state.value.status === 'queued' || state.value.status === 'processing',
  )
  let activeRun: ActiveRun | null = null
  let attempts = 0

  function stopActiveRun() {
    const previous = activeRun
    activeRun = null // 先使旧回调失效，再中止异步操作。
    if (previous) {
      window.clearTimeout(previous.watchdog)
      previous.controller.abort()
    }
  }

  function reset() {
    stopActiveRun()
    attempts = 0
    state.value = {
      status: 'idle',
      task: null,
      message: '选择模拟场景后开始，每次只运行一个本地任务。',
      result: null,
    }
  }

  function cancel() {
    const previous = activeRun
    if (!previous) return
    stopActiveRun()
    state.value = {
      status: 'cancelled',
      task: previous.task,
      message: '本次本地模拟已取消，视频保留，可以重新开始。',
      result: null,
    }
  }

  async function start(
    scenario: MockAnalysisScenario,
    resultPreset: MockResultPreset = 'with_issues',
  ) {
    const input = getInput()
    if (!input || activeRun) return
    const task: MockAnalysisTask = {
      id: `mock-${Date.now().toString(36)}-${++nextTaskNumber}`,
      attempt: ++attempts,
      scenario,
      exerciseId: input.exerciseId,
      exerciseName: input.exerciseName,
      videoName: input.videoName,
      videoKey: input.videoKey,
      durationSeconds: input.durationSeconds,
      resultPreset,
    }
    const run: ActiveRun = { task, controller: new AbortController(), watchdog: 0 }
    activeRun = run
    state.value = {
      status: 'queued',
      task,
      message: '正在演示本地任务排队，视频尚未上传。',
      result: null,
    }
    run.watchdog = window.setTimeout(() => {
      if (activeRun !== run) return
      stopActiveRun()
      state.value = {
        status: 'timed_out',
        task,
        message: `等待超过 ${MOCK_ANALYSIS_TIMEOUT_MS / 1000} 秒，已停止本地模拟。视频保留，可以重试。`,
        result: null,
      }
    }, MOCK_ANALYSIS_TIMEOUT_MS)

    try {
      const outcome = await runMockAnalysisTask(task, {
        signal: run.controller.signal,
        onProcessing: () => {
          if (activeRun !== run) return
          state.value = {
            status: 'processing',
            task,
            message: '正在演示分析处理过程，没有读取视频内容或调用算法。',
            result: null,
          }
        },
      })
      if (activeRun !== run) return
      state.value = {
        status: outcome.status,
        task,
        message: outcome.message,
        result: outcome.result,
      }
    } catch {
      if (activeRun !== run) return
      state.value = {
        status: 'failed',
        task,
        message: '本地模拟任务发生错误，视频保留，请重试。',
        result: null,
      }
    } finally {
      window.clearTimeout(run.watchdog)
      if (activeRun === run) activeRun = null
    }
  }

  // 只观察文件身份和动作，不因时长元信息更新而误取消任务。
  watch(
    () => {
      const input = getInput()
      return input ? JSON.stringify([input.exerciseId, input.videoKey]) : null
    },
    reset,
    { flush: 'sync' },
  )
  onBeforeUnmount(reset)

  return { state, isBusy, start, cancel }
}
