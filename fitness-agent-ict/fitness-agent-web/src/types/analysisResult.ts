export type MockResultPreset = 'with_issues' | 'no_issues' | 'low_confidence'
export type IssueSeverity = 'low' | 'medium' | 'high'

export interface AnalysisIssueTemplate {
  code: string
  title: string
  severity: IssueSeverity
  confidence: number
  startRatio: number
  endRatio: number
  feedback: string
}

export interface AnalysisIssue {
  id: string
  code: string
  title: string
  severity: IssueSeverity
  confidence: number
  startMs: number | null
  endMs: number | null
  feedback: string
}

export interface MockAnalysisResult {
  source: 'local_mock'
  taskId: string
  exerciseId: string
  exerciseName: string
  videoName: string
  preset: MockResultPreset
  durationSeconds: number | null
  issues: AnalysisIssue[]
}

export interface VideoLocationRequest {
  videoKey: string
  exerciseId: string
  taskId: string
  issueId: string
  startMs: number
  endMs: number
}
