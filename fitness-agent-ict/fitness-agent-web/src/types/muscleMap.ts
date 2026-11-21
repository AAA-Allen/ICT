import type { MuscleId } from './muscle'

export type BodyView = 'front' | 'back'

export interface MuscleMapRegion {
  id: MuscleId
  // 绘制身体左半侧，再以中心线镜像到右半侧；两侧共用一个肌群 ID。
  paths: string[]
  label: { x: number; y: number; anchor: 'start' | 'end'; line: string }
}
