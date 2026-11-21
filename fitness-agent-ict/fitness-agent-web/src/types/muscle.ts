// 使用稳定的英文 ID 表示业务数据，中文名称只负责展示。
export type MuscleId =
  | 'chest'
  | 'deltoids'
  | 'trapezius'
  | 'lats'
  | 'biceps'
  | 'triceps'
  | 'abs'
  | 'glutes'
  | 'quads'
  | 'hamstrings'
  | 'calves'

// 小图标按身体大区复用，业务筛选使用上面的细分肌群 ID。
export type MuscleIconKind = 'chest' | 'back' | 'shoulders' | 'arms' | 'abs' | 'legs'

export interface MuscleGroup {
  id: MuscleId
  icon: MuscleIconKind
  name: string
  englishName: string
  caption: string
  description: string
  primaryMuscles: string[]
}
