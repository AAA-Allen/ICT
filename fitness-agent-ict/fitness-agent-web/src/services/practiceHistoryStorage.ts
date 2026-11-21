import { exercises } from '../data/exercises'
import { muscles } from '../data/muscles'
import { equipmentOptions, goalOptions, levelOptions } from '../data/trainingOptions'
import { resultPresets } from '../data/analysisResultOptions'
import { canLocateIssue } from '../utils/videoTime'
import { getLocalExercises } from './exerciseService'
import type { AnalysisIssue, MockAnalysisResult } from '../types/analysisResult'
import type { PracticeRecord } from '../types/practice'
import type { TrainingConfiguration } from '../types/training'

export const HISTORY_STORAGE_KEY = 'fitness-agent.practice-history.v1'
export const MAX_PRACTICE_RECORDS = 20
const MAX_STORAGE_LENGTH = 500_000

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function text(value: unknown, max = 300): value is string {
  return typeof value === 'string' && value.trim().length > 0 && value.length <= max
}

export function isTrainingConfiguration(value: unknown): value is TrainingConfiguration {
  return (
    object(value) &&
    muscles.some((item) => item.id === value.muscleId) &&
    goalOptions.some((item) => item.value === value.goal) &&
    levelOptions.some((item) => item.value === value.level) &&
    equipmentOptions.some((item) => item.value === value.equipment)
  )
}

function isIssue(value: unknown, duration: number | null): value is AnalysisIssue {
  if (
    !object(value) ||
    !text(value.id) ||
    !text(value.code, 100) ||
    !text(value.title) ||
    !['low', 'medium', 'high'].includes(String(value.severity)) ||
    typeof value.confidence !== 'number' ||
    !Number.isFinite(value.confidence) ||
    value.confidence < 0 ||
    value.confidence > 1 ||
    !text(value.feedback, 2000)
  )
    return false
  return (
    (value.startMs === null && value.endMs === null) ||
    (typeof value.startMs === 'number' &&
      typeof value.endMs === 'number' &&
      canLocateIssue(value as unknown as AnalysisIssue, duration))
  )
}

function isResult(value: unknown): value is MockAnalysisResult {
  if (
    !object(value) ||
    value.source !== 'local_mock' ||
    !text(value.taskId) ||
    !text(value.videoName, 1000) ||
    !text(value.exerciseName) ||
    !exercises.some((item) => item.id === value.exerciseId) ||
    !resultPresets.some((item) => item.value === value.preset) ||
    !(
      value.durationSeconds === null ||
      (typeof value.durationSeconds === 'number' &&
        Number.isFinite(value.durationSeconds) &&
        value.durationSeconds > 0)
    ) ||
    !Array.isArray(value.issues) ||
    value.issues.length > 50
  )
    return false
  const ids = new Set<string>()
  return value.issues.every((issue) => {
    if (!isIssue(issue, value.durationSeconds as number | null) || ids.has(issue.id)) return false
    ids.add(issue.id)
    return true
  })
}

export function isPracticeRecord(value: unknown): value is PracticeRecord {
  if (
    !object(value) ||
    !text(value.id) ||
    !text(value.createdAt, 50) ||
    !Number.isFinite(Date.parse(value.createdAt)) ||
    !(
      value.previousRecordId === null ||
      (text(value.previousRecordId) && value.previousRecordId !== value.id)
    ) ||
    !isTrainingConfiguration(value.configuration) ||
    !isResult(value.result) ||
    value.id !== value.result.taskId
  )
    return false
  const result = value.result
  return getLocalExercises(value.configuration).some((item) => item.id === result.exerciseId)
}

// 逐字段重建，丢弃存储中可能混入的 File、临时 URL、未知字段与对象引用。
export function copyPracticeRecord(record: PracticeRecord): PracticeRecord {
  const { result, configuration } = record
  return {
    id: record.id,
    createdAt: record.createdAt,
    previousRecordId: record.previousRecordId,
    configuration: {
      muscleId: configuration.muscleId,
      goal: configuration.goal,
      level: configuration.level,
      equipment: configuration.equipment,
    },
    result: {
      source: 'local_mock',
      taskId: result.taskId,
      exerciseId: result.exerciseId,
      exerciseName: result.exerciseName,
      videoName: result.videoName,
      preset: result.preset,
      durationSeconds: result.durationSeconds,
      issues: result.issues.map((issue) => ({
        id: issue.id,
        code: issue.code,
        title: issue.title,
        severity: issue.severity,
        confidence: issue.confidence,
        startMs: issue.startMs,
        endMs: issue.endMs,
        feedback: issue.feedback,
      })),
    },
  }
}

export function loadPracticeHistory(): { records: PracticeRecord[]; warning: string } {
  try {
    const raw = window.localStorage.getItem(HISTORY_STORAGE_KEY)
    if (!raw) return { records: [], warning: '' }
    if (raw.length > MAX_STORAGE_LENGTH) throw new Error('Oversized history')
    const saved: unknown = JSON.parse(raw)
    if (!object(saved) || saved.version !== 1 || !Array.isArray(saved.records))
      throw new Error('Unsupported history')
    const ids = new Set<string>()
    const records: PracticeRecord[] = []
    let rejected = false
    for (const item of saved.records) {
      if (!isPracticeRecord(item) || ids.has(item.id)) {
        rejected = true
        continue
      }
      ids.add(item.id)
      if (records.length < MAX_PRACTICE_RECORDS) records.push(copyPracticeRecord(item))
    }
    return { records, warning: rejected ? '部分本地记录格式无效，已跳过；有效记录仍可查看。' : '' }
  } catch {
    return {
      records: [],
      warning: '本地历史暂不可读或格式已损坏；可以继续使用，新的成功记录会尝试重新保存。',
    }
  }
}

export function savePracticeHistory(records: PracticeRecord[]): boolean {
  try {
    if (records.length === 0) window.localStorage.removeItem(HISTORY_STORAGE_KEY)
    else {
      const payload = JSON.stringify({
        version: 1,
        records: records.slice(0, MAX_PRACTICE_RECORDS).map(copyPracticeRecord),
      })
      if (payload.length > MAX_STORAGE_LENGTH) return false
      window.localStorage.setItem(HISTORY_STORAGE_KEY, payload)
    }
    return true
  } catch {
    return false
  }
}
