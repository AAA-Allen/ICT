import { MIN_ISSUE_CONFIDENCE } from '../data/analysisResultOptions'
import type { AnalysisIssue } from '../types/analysisResult'

export function isReliableIssue(issue: AnalysisIssue) {
  return (
    Number.isFinite(issue.confidence) &&
    issue.confidence >= MIN_ISSUE_CONFIDENCE &&
    issue.confidence <= 1
  )
}
