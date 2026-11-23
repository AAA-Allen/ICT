const base = 'http://127.0.0.1:8082/api'
const sessionKey = 'fitness-agent.backend-session.v1'

function sessionId() {
  const current = localStorage.getItem(sessionKey)
  if (current) return current
  const id = crypto.randomUUID()
  localStorage.setItem(sessionKey, id)
  return id
}

async function request(path: string, signal: AbortSignal, options: RequestInit = {}) {
  const headers = new Headers(options.headers)
  headers.set('X-Session-Id', sessionId())
  const timeout = AbortSignal.timeout(path === '/videos' ? 185000 : 12000)
  const response = await fetch(base + path, {
    ...options,
    headers,
    signal: AbortSignal.any([signal, timeout]),
  })
  const body = await response.json()
  if (!response.ok) throw new Error(body?.error?.code || `HTTP ${response.status}`)
  return body
}

export const taskLabels = {
  QUEUED: '排队中',
  ANALYZING: '分析中',
  AGENT_PROCESSING: '正在生成反馈',
  COMPLETED: '已完成',
  FAILED: '任务失败',
  CANCELED: '已取消',
}
export interface SquatTask {
  task_id: string
  video_id: string
  exercise_id: 'normal_squat' | 'wide_squat' | 'narrow_squat'
  camera_view?: string
  algorithm?: {
    results?: {
      features?: { cycle_count?: number; peak_flexion_deg?: number }
      quality?: { valid_knee_fraction?: number }
      biomechanics?: {
        dual_analysis?: {
          kinematics: {
            findings: {
              id: string
              problem: string
              label: string
              reason: string
              advice: string
            }[]
          }
        }
        cycles?: {
          cycle_index: number
          boundary_status?: string
          descent_to_bottom_sec?: number
          active_ascent_sec?: number
        }[]
        metrics?: Record<
          string,
          {
            description: string
            status: string
            median: number | null
            unit: string
            reason_code?: string
            p05?: number | null
            p95?: number | null
            coverage?: number
          }
        >
      }
    }
    guidance?: {
      observations?: {
        type: string
        status: string
        reason?: string
        reason_code?: string
        events?: SquatEvent[]
        review_events?: SquatEvent[]
      }[]
    }
  } | null
  agent?: { summary: string; suggestions: { text: string; evidence_paths: string[] }[] } | null
  status: keyof typeof taskLabels
  reason_code?: string | null
  agent_status?: string
  cancel_requested?: boolean
}
export interface SquatEvent {
  start_sec: number
  end_sec: number
  side?: string
  phase?: string
  cycle_index?: number | null
  evidence?: Record<string, unknown>
}
export const terminal = (t: SquatTask) => ['FAILED', 'CANCELED', 'COMPLETED'].includes(t.status)

export async function synthesizeSpeech(text: string, signal: AbortSignal) {
  const result = await request('/speech', signal, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
  if (result.mime_type !== 'audio/mpeg' || typeof result.audio_base64 !== 'string')
    throw new Error('语音服务返回格式不正确')
  return result as { audio_base64: string; mime_type: string }
}

function parseTask(value: unknown, video?: string, id?: string): SquatTask {
  const t = value as Partial<SquatTask> | null
  if (
    !t ||
    typeof t.task_id !== 'string' ||
    !t.task_id ||
    typeof t.video_id !== 'string' ||
    !t.video_id ||
    !['normal_squat', 'wide_squat', 'narrow_squat'].includes(t.exercise_id ?? '') ||
    !Object.hasOwn(taskLabels, t.status ?? '') ||
    (id && t.task_id !== id) ||
    (video && t.video_id !== video)
  )
    throw new Error('任务响应与当前练习不一致')
  return t as SquatTask
}

export async function uploadSquat(file: File, key: string, signal: AbortSignal) {
  if (!['video/mp4', 'video/webm'].includes(file.type))
    throw new Error('真实上传需要 video/mp4 或 video/webm 类型的文件')
  const result = await request('/videos', signal, {
    method: 'POST',
    headers: { 'Content-Type': file.type, 'X-Upload-Key': key },
    body: file,
  })
  if (typeof result.video_id !== 'string' || !result.video_id)
    throw new Error('上传响应缺少视频编号')
  return result.video_id as string
}
export async function createSquat(
  video: string,
  key: string,
  signal: AbortSignal,
  action: SquatTask['exercise_id'] = 'normal_squat',
  cameraView = 'unknown',
) {
  const data = await request('/analyses', signal, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      video_id: video,
      exercise_id: action,
      camera_view: cameraView,
      idempotency_key: key,
    }),
  })
  return parseTask(data.task, video)
}
export async function readSquat(t: SquatTask, signal: AbortSignal, cancel = false) {
  const path = '/analyses/' + encodeURIComponent(t.task_id) + (cancel ? '/cancel' : '')
  return parseTask(
    (await request(path, signal, { method: cancel ? 'POST' : 'GET' })).task,
    t.video_id,
    t.task_id,
  )
}
export async function listSquats(signal: AbortSignal) {
  const data = await request('/analyses?limit=20', signal)
  if (!Array.isArray(data.tasks)) throw new Error('无法读取任务列表')
  return data.tasks.map((t: unknown) => parseTask(t)) as SquatTask[]
}
