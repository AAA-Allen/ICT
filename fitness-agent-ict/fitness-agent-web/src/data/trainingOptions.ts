import type { Equipment, TrainingGoal, TrainingLevel, TrainingPreferences } from '../types/training'

export const goalOptions: { value: TrainingGoal; label: string }[] = [
  { value: 'strength', label: '增强力量' },
  { value: 'endurance', label: '提升耐力' },
  { value: 'general_fitness', label: '日常健身' },
]

export const levelOptions: { value: TrainingLevel; label: string }[] = [
  { value: 'beginner', label: '初学者' },
  { value: 'intermediate', label: '有训练经验' },
  { value: 'advanced', label: '熟练训练者' },
]

export const equipmentOptions: { value: Equipment; label: string }[] = [
  { value: 'none', label: '无器械' },
  { value: 'dumbbell', label: '哑铃' },
  { value: 'resistance_band', label: '弹力带' },
]

// 每次返回一个新对象，恢复默认时不会复用之前被修改的对象。
export function createDefaultPreferences(): TrainingPreferences {
  return { goal: 'strength', level: 'beginner', equipment: 'none' }
}
