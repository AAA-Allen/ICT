import { MOCK_PROCESSING_DELAY_MS, MOCK_QUEUE_DELAY_MS } from '../data/analysisScenarios'
import type { MockAnalysisOutcome, MockAnalysisTask } from '../types/analysis'
import { buildMockAnalysisResult } from './mockAnalysisResult'

function abortError() {
  return new DOMException('Local mock task cancelled', 'AbortError')
}

function delay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(abortError())
      return
    }
    const timer = window.setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve()
    }, milliseconds)
    function onAbort() {
      window.clearTimeout(timer)
      signal.removeEventListener('abort', onAbort)
      reject(abortError())
    }
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

function waitUntilAborted(signal: AbortSignal): Promise<never> {
  return new Promise((_, reject) => {
    if (signal.aborted) reject(abortError())
    else signal.addEventListener('abort', () => reject(abortError()), { once: true })
  })
}

// 只演示异步状态。不读取视频内容、不发送请求、不调用姿态算法或模型。
export async function runMockAnalysisTask(
  task: MockAnalysisTask,
  options: { signal: AbortSignal; onProcessing: () => void },
): Promise<MockAnalysisOutcome> {
  await delay(MOCK_QUEUE_DELAY_MS, options.signal)
  options.onProcessing()
  if (task.scenario === 'timeout') return waitUntilAborted(options.signal)
  await delay(MOCK_PROCESSING_DELAY_MS, options.signal)

  switch (task.scenario) {
    case 'success':
      return {
        status: 'completed',
        message: '本地模拟任务已完成，返回固定的结构化样例，没有识别视频内容。',
        result: buildMockAnalysisResult(task),
      }
    case 'failure':
      return {
        status: 'failed',
        message: '模拟服务返回失败。视频已保留，可改选模拟场景后重试。',
        result: null,
      }
    case 'insufficient_quality':
      return {
        status: 'insufficient_quality',
        message: '本场景演示拍摄质量不足，不提供动作判断。视频已保留。',
        result: null,
      }
  }
}
