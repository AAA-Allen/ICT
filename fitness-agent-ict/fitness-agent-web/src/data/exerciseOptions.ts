import type { TrainingLevel } from '../types/training'

export const exerciseDifficulties: { value: TrainingLevel; label: string }[] = [
  { value: 'beginner', label: '入门' },
  { value: 'intermediate', label: '进阶' },
  { value: 'advanced', label: '挑战' },
]
