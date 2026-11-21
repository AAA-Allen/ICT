import type { AnalysisIssue, IssueSeverity } from './analysisResult'

export type DemonstrationScenario = 'normal' | 'no_match' | 'query_failure' | 'playback_failure'

export interface DemonstrationRecord {
  id: string
  exerciseId: string
  issueCode: string
  severities: IssueSeverity[]
  title: string
  description: string
  media: { kind: 'playback_sample'; src: string; description: string } | null
}

export interface DemonstrationQuery {
  catalogRevision?: number
  taskId: string
  exerciseId: string
  exerciseName: string
  issue: AnalysisIssue
  scenario: DemonstrationScenario
}

export interface DemonstrationMatch {
  source: 'local_rules' | 'java_catalog_snapshot'
  record: DemonstrationRecord
  reasons: string[]
}

export type DemonstrationMatchState =
  | { status: 'idle' | 'loading' | 'no_match' | 'failed'; match: null }
  | { status: 'matched'; match: DemonstrationMatch }
