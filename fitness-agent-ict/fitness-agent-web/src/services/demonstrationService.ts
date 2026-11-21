import { catalogSource, queryCatalog } from './catalogSource'
import { issueSeverityLabels } from '../data/analysisResultOptions'
import { isReliableIssue } from '../utils/issueConfidence'
import type { DemonstrationMatch, DemonstrationQuery } from '../types/demonstration'

export async function findLocalDemonstration(
  query: DemonstrationQuery,
  signal: AbortSignal,
): Promise<DemonstrationMatch | null> {
  // 只模拟查询耗时；取消时连同计时器与监听一起清理，没有 HTTP 请求。
  await new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException('Aborted', 'AbortError'))
      return
    }
    const abort = () => {
      window.clearTimeout(timer)
      signal.removeEventListener('abort', abort)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    const timer = window.setTimeout(() => {
      signal.removeEventListener('abort', abort)
      resolve()
    }, 350)
    signal.addEventListener('abort', abort, { once: true })
  })
  if (signal.aborted) throw new DOMException('Aborted', 'AbortError')
  if (!isReliableIssue(query.issue)) return null
  if (query.scenario === 'query_failure') throw new Error('Local example query failed')
  if (query.scenario === 'no_match') return null
  const record = queryCatalog.value.demonstrations.find(
    (item) =>
      item.exerciseId === query.exerciseId &&
      item.issueCode === query.issue.code &&
      item.severities.includes(query.issue.severity),
  )
  if (!record) return null
  return {
    source: catalogSource.value === 'java' ? 'java_catalog_snapshot' : 'local_rules',
    record,
    reasons: [
      `动作相同：${query.exerciseName}`,
      `问题代码相同：${query.issue.code}`,
      `示例程度适用：${issueSeverityLabels[query.issue.severity]}`,
    ],
  }
}
