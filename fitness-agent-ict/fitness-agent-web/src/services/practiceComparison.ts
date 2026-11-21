import { isReliableIssue } from '../utils/issueConfidence'
import type { AnalysisIssue, IssueSeverity } from '../types/analysisResult'
import type { ComparedIssue, ComparedIssueSide, PracticeRecord } from '../types/practice'
import type { TrainingConfiguration } from '../types/training'

export function sameTrainingConfiguration(a: TrainingConfiguration, b: TrainingConfiguration) {
  return (
    a.muscleId === b.muscleId &&
    a.goal === b.goal &&
    a.level === b.level &&
    a.equipment === b.equipment
  )
}

export function comparisonBlockReason(before: PracticeRecord, after: PracticeRecord): string {
  if (before.id === after.id) return '请选择两条不同的记录。'
  if (before.result.exerciseId !== after.result.exerciseId)
    return '动作不同，暂不比较。请选择同一动作的两条记录。'
  if (!sameTrainingConfiguration(before.configuration, after.configuration))
    return '训练条件不同，暂不比较。请选择肌群、目标、经验和器械一致的记录。'
  if (Date.parse(before.createdAt) > Date.parse(after.createdAt))
    return '前后顺序相反，请交换两条记录。'
  return ''
}

function summarize(issues: AnalysisIssue[]): ComparedIssueSide {
  const reliable = issues.filter(isReliableIssue)
  const ranks: Record<IssueSeverity, number> = { low: 1, medium: 2, high: 3 }
  let severity: IssueSeverity | null = null
  for (const issue of reliable) {
    if (!severity || ranks[issue.severity] > ranks[severity]) severity = issue.severity
  }
  return { count: issues.length, reliableCount: reliable.length, severity }
}

export function comparePracticeRecords(
  before: PracticeRecord,
  after: PracticeRecord,
): ComparedIssue[] {
  if (comparisonBlockReason(before, after)) return []
  const allIssues = [...before.result.issues, ...after.result.issues]
  const codes = [...new Set(allIssues.map((issue) => issue.code))]
  return codes.map((code) => {
    const a = summarize(before.result.issues.filter((issue) => issue.code === code))
    const b = summarize(after.result.issues.filter((issue) => issue.code === code))
    const uncertain = a.count > a.reliableCount || b.count > b.reliableCount
    return {
      code,
      title: allIssues.find((issue) => issue.code === code)!.title,
      before: a,
      after: b,
      status: uncertain
        ? 'uncertain'
        : a.count && b.count
          ? 'both'
          : a.count
            ? 'before_only'
            : 'after_only',
    }
  })
}
