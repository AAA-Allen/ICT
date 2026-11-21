import type { MockAnalysisResult, MockResultPreset } from './analysisResult'

export type MockAnalysisScenario = 'success' | 'failure' | 'timeout' | 'insufficient_quality'

export type AnalysisTaskStatus =
  | 'idle'
  | 'queued'
  | 'processing'
  | 'completed'
  | 'failed'
  | 'timed_out'
  | 'insufficient_quality'
  | 'cancelled'

export interface AnalysisInput {
  videoKey: string
  videoName: string
  exerciseId: string
  exerciseName: string
  durationSeconds: number | null
}

export interface MockAnalysisTask {
  id: string
  attempt: number
  scenario: MockAnalysisScenario
  exerciseId: string
  exerciseName: string
  videoName: string
  videoKey: string
  durationSeconds: number | null
  resultPreset: MockResultPreset
}

export type MockAnalysisOutcome =
  | { status: 'completed'; message: string; result: MockAnalysisResult }
  | { status: 'failed' | 'insufficient_quality'; message: string; result: null }

export interface AnalysisTaskState {
  status: AnalysisTaskStatus
  task: MockAnalysisTask | null
  message: string
  result: MockAnalysisResult | null
}
