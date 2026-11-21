import type { AnalysisIssueTemplate } from '../types/analysisResult'

// 固定的前端展示样例。标题、置信度和反馈都不是当前视频的识别结论。
const genericIssues: AnalysisIssueTemplate[] = [
  {
    code: 'RANGE_VARIATION',
    title: '动作幅度差异',
    severity: 'medium',
    confidence: 0.89,
    startRatio: 0.18,
    endRatio: 0.4,
    feedback: '示例复核方向：回看标记时段，对照同一动作的示范资料观察幅度。',
  },
  {
    code: 'TEMPO_VARIATION',
    title: '动作节奏变化',
    severity: 'low',
    confidence: 0.76,
    startRatio: 0.58,
    endRatio: 0.82,
    feedback: '示例复核方向：比较动作不同阶段的节奏，并记录需要进一步确认的位置。',
  },
]

const squatIssues: AnalysisIssueTemplate[] = [
  {
    code: 'DEPTH_INSUFFICIENT',
    title: '下蹲幅度观察',
    severity: 'medium',
    confidence: 0.89,
    startRatio: 0.18,
    endRatio: 0.4,
    feedback: '示例反馈：回看下蹲阶段，对照同一动作的示范资料复核幅度。',
  },
  {
    code: 'TRUNK_CONTROL',
    title: '躯干稳定性观察',
    severity: 'low',
    confidence: 0.76,
    startRatio: 0.58,
    endRatio: 0.82,
    feedback: '示例反馈：回看标记阶段的躯干位置变化，先核对拍摄视角，再对照示范资料。',
  },
]

const pushUpIssues: AnalysisIssueTemplate[] = [
  {
    code: 'TRUNK_ALIGNMENT',
    title: '躯干姿态观察',
    severity: 'medium',
    confidence: 0.89,
    startRatio: 0.18,
    endRatio: 0.4,
    feedback: '示例反馈：回看支撑阶段，观察躯干姿态并与同一动作的示范资料对照。',
  },
  {
    code: 'LOWERING_TEMPO',
    title: '下降节奏观察',
    severity: 'low',
    confidence: 0.76,
    startRatio: 0.58,
    endRatio: 0.82,
    feedback: '示例反馈：回看下降阶段，比较不同阶段的动作节奏。',
  },
]

export function getMockIssueTemplates(exerciseId: string): AnalysisIssueTemplate[] {
  if (['bodyweight_squat', 'goblet_squat'].includes(exerciseId)) return squatIssues
  if (['wall_push_up', 'kneeling_push_up', 'push_up'].includes(exerciseId)) return pushUpIssues
  return genericIssues
}
