import { ref, shallowRef } from 'vue'
import {
  copyPracticeRecord,
  isPracticeRecord,
  loadPracticeHistory,
  MAX_PRACTICE_RECORDS,
  savePracticeHistory,
} from '../services/practiceHistoryStorage'
import { comparisonBlockReason } from '../services/practiceComparison'
import type { CompletedPractice, PracticeRecord } from '../types/practice'

export function usePracticeHistory() {
  const loaded = loadPracticeHistory()
  const records = shallowRef(loaded.records)
  const storageWarning = ref(loaded.warning)
  const message = ref('')
  // 已记录的任务即使被用户删除，也不能因一次重复完成事件重新出现。
  const recordedTaskIds = new Set(records.value.map((record) => record.id))

  function persist() {
    const saved = savePracticeHistory(records.value)
    storageWarning.value = saved
      ? ''
      : '浏览器未能保存更改，当前列表仅在本页有效；刷新后可能恢复旧记录。可稍后点击“重试保存”。'
  }

  function add(
    completed: CompletedPractice,
    previousRecordId: string | null,
  ): PracticeRecord | null {
    if (recordedTaskIds.has(completed.result.taskId)) return null
    const record: PracticeRecord = {
      id: completed.result.taskId,
      createdAt: new Date().toISOString(),
      previousRecordId: null,
      configuration: completed.configuration,
      result: completed.result,
    }
    if (!isPracticeRecord(record)) {
      message.value = '这份结果不符合本地记录格式，未保存。'
      return null
    }
    const previous = records.value.find((item) => item.id === previousRecordId)
    if (previous && !comparisonBlockReason(previous, record)) record.previousRecordId = previous.id
    const snapshot = copyPracticeRecord(record)
    records.value = [snapshot, ...records.value].slice(0, MAX_PRACTICE_RECORDS)
    recordedTaskIds.add(record.id)
    persist()
    message.value = `已记录本次模拟结果，最多保留最近 ${MAX_PRACTICE_RECORDS} 条。`
    return snapshot
  }

  function remove(id: string) {
    if (!records.value.some((record) => record.id === id)) return
    records.value = records.value.filter((record) => record.id !== id)
    persist()
    message.value = '已从当前列表删除该记录。'
  }

  function clear() {
    records.value = []
    persist()
    message.value = '已清空当前本地记录列表。'
  }

  return { records, storageWarning, message, add, remove, clear, persist }
}
