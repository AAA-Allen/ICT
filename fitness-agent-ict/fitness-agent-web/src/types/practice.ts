import type { MockAnalysisResult, IssueSeverity } from './analysisResult'
import type { TrainingConfiguration } from './training'

export interface CompletedPractice {
  result: MockAnalysisResult
  configuration: TrainingConfiguration
}

export interface PracticeRecord extends CompletedPractice {
  id: string
  createdAt: string
  previousRecordId: string | null
}

export interface PracticeAgainRequest {
  recordId: string
  sequence: number
}

export interface PracticeComparisonPair {
  beforeId: string
  afterId: string
}

export interface ComparedIssueSide {
  count: number
  reliableCount: number
  severity: IssueSeverity | null
}

export interface ComparedIssue {
  code: string
  title: string
  before: ComparedIssueSide
  after: ComparedIssueSide
  status: 'both' | 'before_only' | 'after_only' | 'uncertain'
}
