import { muscles } from '../data/muscles'
import { equipmentOptions, goalOptions, levelOptions } from '../data/trainingOptions'
import { demonstrations } from '../data/demonstrations'
import type { QueryCatalog } from '../types/catalog'
import type { MuscleGroup, MuscleId } from '../types/muscle'
import type { Exercise } from '../types/exercise'
import type { DemonstrationRecord } from '../types/demonstration'

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
function text(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0 && value.length <= 3000
}
function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.length > 0 && value.length <= 50 && value.every(text)
}
function entries(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value) || value.length > 1000 || !value.every(object))
    throw new Error('资料集合格式不正确。')
  const ids = new Set<string>()
  for (const item of value) {
    if (!text(item.id) || ids.has(item.id)) throw new Error('资料 ID 缺失或重复。')
    ids.add(item.id)
  }
  return value
}
const muscleIds = new Set(muscles.map((item) => item.id))

export function parseJavaCatalog(payload: unknown): QueryCatalog {
  if (
    !object(payload) ||
    !object(payload.meta) ||
    payload.meta.source !== 'local_catalog' ||
    payload.meta.contractVersion !== 'personal-v1' ||
    !object(payload.data) ||
    payload.data.contractVersion !== 'personal-v1'
  )
    throw new Error('服务返回的个人协议版本不兼容。')
  const data = payload.data
  const muscleEntries = entries(data.muscles)
  if (muscleEntries.length !== muscleIds.size) throw new Error('肌群资料不完整。')
  const parsedMuscles: MuscleGroup[] = muscleEntries.map((item) => {
    if (
      !muscleIds.has(item.id as MuscleId) ||
      !text(item.name) ||
      !text(item.englishName) ||
      !text(item.caption) ||
      !text(item.description) ||
      !strings(item.primaryMuscles) ||
      !['chest', 'back', 'shoulders', 'arms', 'abs', 'legs'].includes(String(item.icon))
    )
      throw new Error('肌群字段不符合本地协议。')
    return {
      id: item.id as MuscleId,
      name: item.name,
      englishName: item.englishName,
      icon: item.icon as MuscleGroup['icon'],
      caption: item.caption,
      description: item.description,
      primaryMuscles: [...item.primaryMuscles],
    }
  })
  const parsedExercises: Exercise[] = entries(data.exercises).map((item) => {
    if (
      !text(item.name) ||
      !text(item.description) ||
      !strings(item.targetMuscles) ||
      !item.targetMuscles.every((id) => muscleIds.has(id as MuscleId)) ||
      !levelOptions.some((option) => option.value === item.difficulty) ||
      !equipmentOptions.some((option) => option.value === item.equipment) ||
      !strings(item.goals) ||
      !item.goals.every((goal) => goalOptions.some((option) => option.value === goal))
    )
      throw new Error('动作字段不符合本地协议。')
    return {
      id: item.id as string,
      name: item.name,
      description: item.description,
      targetMuscles: [...item.targetMuscles] as MuscleId[],
      difficulty: item.difficulty as Exercise['difficulty'],
      equipment: item.equipment as Exercise['equipment'],
      goals: [...item.goals] as Exercise['goals'],
    }
  })
  if (parsedExercises.length === 0) throw new Error('动作资料不能为空。')
  const exerciseIds = new Set(parsedExercises.map((item) => item.id))
  const parsedDemonstrations: DemonstrationRecord[] = entries(data.demonstrations).map((item) => {
    if (
      !text(item.title) ||
      !text(item.description) ||
      !text(item.issueCode) ||
      !exerciseIds.has(String(item.exerciseId)) ||
      !strings(item.severities) ||
      !item.severities.every((value) => ['low', 'medium', 'high'].includes(value))
    )
      throw new Error('示范资料字段不符合本地协议。')
    let media: DemonstrationRecord['media'] = null
    if (item.media !== null) {
      if (
        !object(item.media) ||
        item.media.kind !== 'playback_sample' ||
        item.media.assetKey !== 'playback-sample' ||
        !text(item.media.description)
      )
        throw new Error('示范资料包含不支持的媒体类型。')
      const bundled = demonstrations.find((record) => record.media)?.media
      if (!bundled) throw new Error('本地测试素材未找到。')
      media = { ...bundled, description: item.media.description }
    }
    return {
      id: item.id as string,
      exerciseId: item.exerciseId as string,
      issueCode: item.issueCode,
      severities: [...item.severities] as DemonstrationRecord['severities'],
      title: item.title,
      description: item.description,
      media,
    }
  })
  return {
    muscles: parsedMuscles,
    exercises: parsedExercises,
    demonstrations: parsedDemonstrations,
  }
}
