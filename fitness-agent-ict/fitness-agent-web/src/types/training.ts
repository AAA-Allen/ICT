import type { MuscleId } from './muscle'

export type TrainingGoal = 'strength' | 'endurance' | 'general_fitness'
export type TrainingLevel = 'beginner' | 'intermediate' | 'advanced'
export type Equipment = 'none' | 'dumbbell' | 'resistance_band'

export interface TrainingPreferences {
  goal: TrainingGoal
  level: TrainingLevel
  equipment: Equipment
}

// 当前只供本地页面使用，正式 API 字段需和队友确认。
export interface TrainingConfiguration extends TrainingPreferences {
  muscleId: MuscleId
}
