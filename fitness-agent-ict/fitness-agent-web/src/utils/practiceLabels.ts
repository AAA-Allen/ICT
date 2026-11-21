import { muscles } from '../data/muscles'
import { equipmentOptions, goalOptions, levelOptions } from '../data/trainingOptions'
import { resultPresets } from '../data/analysisResultOptions'
import type { PracticeRecord } from '../types/practice'
import type { TrainingConfiguration } from '../types/training'

export function configurationLabels(configuration: TrainingConfiguration): string[] {
  return [
    muscles.find((item) => item.id === configuration.muscleId)?.name ?? '',
    goalOptions.find((item) => item.value === configuration.goal)?.label ?? '',
    levelOptions.find((item) => item.value === configuration.level)?.label ?? '',
    equipmentOptions.find((item) => item.value === configuration.equipment)?.label ?? '',
  ]
}

export function formatPracticeDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(value))
}

export function practicePresetLabel(record: PracticeRecord) {
  return resultPresets.find((item) => item.value === record.result.preset)?.label ?? ''
}

export function practiceOptionLabel(record: PracticeRecord) {
  return `${formatPracticeDate(record.createdAt)} · ${record.result.exerciseName} · ${practicePresetLabel(record)} · ${record.id.slice(-6)}`
}
