import { getMockIssueTemplates } from '../data/mockAnalysisIssues'
import type { MockAnalysisTask } from '../types/analysis'
import type { MockAnalysisResult } from '../types/analysisResult'

export function buildMockAnalysisResult(task: MockAnalysisTask): MockAnalysisResult {
  const durationSeconds =
    task.durationSeconds !== null &&
    Number.isFinite(task.durationSeconds) &&
    task.durationSeconds > 0
      ? task.durationSeconds
      : null
  const totalMs = durationSeconds === null ? null : Math.floor(durationSeconds * 1000)
  const templates = task.resultPreset === 'no_issues' ? [] : getMockIssueTemplates(task.exerciseId)

  return {
    source: 'local_mock',
    taskId: task.id,
    exerciseId: task.exerciseId,
    exerciseName: task.exerciseName,
    videoName: task.videoName,
    preset: task.resultPreset,
    durationSeconds,
    issues: templates.map((template, index) => {
      // 只用时长缩放固定样例，确保区间不超出视频；不分析画面。
      const startMs =
        totalMs !== null && totalMs >= 1
          ? Math.min(totalMs - 1, Math.max(0, Math.floor(totalMs * template.startRatio)))
          : null
      const endMs =
        startMs !== null && totalMs !== null
          ? Math.min(totalMs, Math.max(startMs + 1, Math.ceil(totalMs * template.endRatio)))
          : null
      return {
        id: `${task.id}-issue-${index + 1}`,
        code: template.code,
        title: template.title,
        severity: template.severity,
        confidence:
          task.resultPreset === 'low_confidence' ? [0.42, 0.55][index % 2]! : template.confidence,
        startMs,
        endMs,
        feedback: template.feedback,
      }
    }),
  }
}
