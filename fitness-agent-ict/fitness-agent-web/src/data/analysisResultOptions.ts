import type { IssueSeverity, MockResultPreset } from '../types/analysisResult'

export const MIN_ISSUE_CONFIDENCE = 0.6

export const resultPresets: { value: MockResultPreset; label: string }[] = [
  { value: 'with_issues', label: '问题示例' },
  { value: 'no_issues', label: '无问题记录示例' },
  { value: 'low_confidence', label: '低置信度示例' },
]

export const issueSeverityLabels: Record<IssueSeverity, string> = {
  low: '低',
  medium: '中',
  high: '高',
}
