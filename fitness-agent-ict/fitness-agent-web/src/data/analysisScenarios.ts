import type { AnalysisTaskStatus, MockAnalysisScenario } from '../types/analysis'

export const MOCK_QUEUE_DELAY_MS = 600
export const MOCK_PROCESSING_DELAY_MS = 1400
export const MOCK_ANALYSIS_TIMEOUT_MS = 4500

export const analysisScenarios: { value: MockAnalysisScenario; label: string }[] = [
  { value: 'success', label: '正常完成（模拟）' },
  { value: 'failure', label: '服务失败（模拟）' },
  { value: 'timeout', label: '等待超时（模拟）' },
  { value: 'insufficient_quality', label: '拍摄质量不足（模拟）' },
]

export const analysisStatusLabels: Record<AnalysisTaskStatus, string> = {
  idle: '等待开始',
  queued: '本地模拟排队中',
  processing: '本地模拟处理中',
  completed: '模拟任务完成',
  failed: '模拟服务失败',
  timed_out: '模拟任务已超时',
  insufficient_quality: '模拟拍摄质量不足',
  cancelled: '模拟任务已取消',
}
