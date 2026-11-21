import type { DemonstrationRecord } from '../types/demonstration'

// 原创文字/图形测试片段，只有播放器用途，没有任何动作教学内容。
const playbackSample = {
  kind: 'playback_sample' as const,
  src: new URL('../assets/media/playback-sample.mp4', import.meta.url).href,
  description: '无声文字与动态图形，用于测试播放、暂停和拖动进度；不包含运动示范。',
}

// 仅为个人模块的本地规则资料，不是正式团队素材库。
export const demonstrations: DemonstrationRecord[] = [
  {
    id: 'squat-depth-example',
    exerciseId: 'bodyweight_squat',
    issueCode: 'DEPTH_INSUFFICIENT',
    severities: ['medium'],
    title: '徒手深蹲 · 幅度观察资料（样例）',
    description: '预留同一动作、同一问题的资料位置。正式教学视频待个人模块完成后统一补充。',
    media: playbackSample,
  },
  {
    id: 'squat-control-example',
    exerciseId: 'bodyweight_squat',
    issueCode: 'TRUNK_CONTROL',
    severities: ['low'],
    title: '徒手深蹲 · 躯干观察资料（样例）',
    description: '已有本地资料条目，视频素材尚未补充。不会借用其他问题的视频。',
    media: null,
  },
  {
    id: 'wall-push-up-alignment-example',
    exerciseId: 'wall_push_up',
    issueCode: 'TRUNK_ALIGNMENT',
    severities: ['medium'],
    title: '墙壁俯卧撑 · 姿态观察资料（样例）',
    description: '展示按动作与问题匹配资料的界面；这里没有接入真实 Agent 推荐。',
    media: playbackSample,
  },
]

export const demonstrationScenarios = [
  { value: 'normal', label: '正常查询（本地样例）' },
  { value: 'no_match', label: '无匹配资料（模拟）' },
  { value: 'query_failure', label: '资料查询失败（模拟）' },
  { value: 'playback_failure', label: '播放器加载失败（模拟）' },
] as const
