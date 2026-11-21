import type { AnalysisIssue } from '../types/analysisResult'

export function formatVideoTime(milliseconds: number | null): string {
  if (milliseconds === null || !Number.isFinite(milliseconds)) return '未知'
  const total = Math.max(0, Math.round(milliseconds))
  const minutes = Math.floor(total / 60000)
  const seconds = Math.floor((total % 60000) / 1000)
  const hundredths = Math.floor((total % 1000) / 10)
  return `${minutes}:${String(seconds).padStart(2, '0')}.${String(hundredths).padStart(2, '0')}`
}

export function canLocateIssue(
  issue: AnalysisIssue,
  durationSeconds: number | null,
): issue is AnalysisIssue & { startMs: number; endMs: number } {
  return (
    durationSeconds !== null &&
    Number.isFinite(durationSeconds) &&
    durationSeconds > 0 &&
    issue.startMs !== null &&
    issue.endMs !== null &&
    Number.isFinite(issue.startMs) &&
    Number.isFinite(issue.endMs) &&
    issue.startMs >= 0 &&
    issue.endMs > issue.startMs &&
    issue.endMs <= Math.floor(durationSeconds * 1000)
  )
}
