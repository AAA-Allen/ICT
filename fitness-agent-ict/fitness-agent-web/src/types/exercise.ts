import type { MuscleId } from './muscle'
import type { Equipment, TrainingGoal, TrainingLevel } from './training'

export interface Exercise {
  id: string
  name: string
  targetMuscles: MuscleId[]
  difficulty: TrainingLevel
  equipment: Equipment
  goals: TrainingGoal[]
  description: string
}
