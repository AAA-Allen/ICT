<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { createCoachSpeech } from '../services/speech'
import SquatFeedbackResults from './SquatFeedbackResults.vue'
import {
  createSquat,
  listSquats,
  readSquat,
  taskLabels,
  terminal,
  uploadSquat,
  type SquatTask,
} from '../services/squatBackend'

const props = defineProps<{ file?: File; ready: boolean }>()
const emit = defineEmits<{ 'busy-change': [busy: boolean] }>()
const busy = ref(false)
const coachSpeech = createCoachSpeech()
const speechNote = ref('')
async function playReminder(text: string) {
  speechNote.value = '正在合成或播放…'
  try {
    await coachSpeech.speak(text)
    speechNote.value = '已发起播放；可点击停止语音。'
  } catch (error) {
    speechNote.value = error instanceof Error ? error.message : '语音播放失败'
  }
}
function stopReminder() {
  coachSpeech.stop()
  speechNote.value = '语音已停止。'
}
const action = ref<SquatTask['exercise_id']>('normal_squat')
const cameraView = ref('unknown')
const message = ref('选择可预览的徒手深蹲视频后，可以上传到本机业务服务。')
const videoId = ref('')
const task = ref<SquatTask | null>(null)
const history = ref<SquatTask[] | null>(null)
const historyNote = ref('')
let uploadKey = crypto.randomUUID()
let practiceKey = crypto.randomUUID()
let run: AbortController | null = null
let lookup: AbortController | null = null
let timer: ReturnType<typeof setTimeout>
const canCancel = computed(
  () => task.value && !terminal(task.value) && !task.value.cancel_requested,
)
watch(busy, (value) => emit('busy-change', value), { flush: 'sync' })

function stop() {
  clearTimeout(timer)
  run?.abort()
  run = null
}
function begin() {
  stop()
  run = new AbortController()
  busy.value = true
  return run.signal
}
const reasonText: Record<string, string> = {
  ALGORITHM_NOT_CONFIGURED: '后端尚未配置算法',
  QUALITY_INSUFFICIENT: '拍摄质量不足，请重新拍摄',
  VIDEO_UNAVAILABLE: '服务端视频不可用',
}
function accept(next: SquatTask, signal: AbortSignal) {
  if (signal.aborted) return
  task.value = next
  message.value = taskLabels[next.status]
  if (next.reason_code) message.value += ' · ' + (reasonText[next.reason_code] ?? next.reason_code)
  if (next.status === 'COMPLETED')
    message.value +=
      next.agent_status === 'COMPLETED' ? ' · 算法与 Agent 已完成' : ' · 训练反馈已整理'
  if (terminal(next)) {
    busy.value = false
    return
  }
  if (next.cancel_requested) message.value = '取消请求已提交，等待后端确认'
  timer = setTimeout(() => void query(signal), 2000)
}
function fail(e: unknown, signal: AbortSignal) {
  if (signal.aborted) return
  busy.value = false
  const detail = e instanceof Error ? e.message : String(e)
  if (detail === 'VIDEO_UNAVAILABLE') {
    videoId.value = ''
    uploadKey = crypto.randomUUID()
    practiceKey = crypto.randomUUID()
  }
  message.value =
    e instanceof TypeError ? '无法连接业务服务，请检查 8082 端口和浏览器地址后重试。' : detail
}
async function query(signal: AbortSignal, cancel = false) {
  try {
    if (task.value) accept(await readSquat(task.value, signal, cancel), signal)
  } catch (e) {
    fail(e, signal)
  }
}
async function start() {
  if (busy.value || !props.file || !props.ready) return
  const signal = begin()
  if (task.value && !terminal(task.value)) {
    await query(signal)
    return
  }
  if (task.value) {
    task.value = null
    practiceKey = crypto.randomUUID()
  }
  try {
    if (!videoId.value) {
      message.value = '正在上传视频到本机业务服务…'
      const id = await uploadSquat(props.file, uploadKey, signal)
      if (signal.aborted) return
      videoId.value = id
    }
    message.value = '视频已上传，正在创建任务…'
    accept(
      await createSquat(videoId.value, practiceKey, signal, action.value, cameraView.value),
      signal,
    )
  } catch (e) {
    fail(e, signal)
  }
}
async function loadHistory() {
  lookup?.abort()
  const signal = (lookup = new AbortController()).signal
  historyNote.value = '正在查询…'
  try {
    const rows = await listSquats(signal)
    if (!signal.aborted) {
      history.value = rows
      historyNote.value = rows.length ? '最近的会话任务' : '此会话还没有任务'
    }
  } catch (e) {
    if (!signal.aborted) historyNote.value = e instanceof Error ? e.message : '查询失败'
  }
}
async function showHistoryResult(row: SquatTask) {
  if (busy.value) return
  lookup?.abort()
  const signal = (lookup = new AbortController()).signal
  try {
    const result = await readSquat(row, signal)
    if (!signal.aborted) {
      task.value = result
      message.value = '已打开历史练习反馈'
    }
  } catch (e) {
    if (!signal.aborted) historyNote.value = e instanceof Error ? e.message : '读取结果失败'
  }
}
onBeforeUnmount(() => {
  coachSpeech.stop()
  stop()
  lookup?.abort()
  if (task.value && !terminal(task.value))
    void readSquat(task.value, AbortSignal.timeout(5000), true).catch(() => {})
  emit('busy-change', false)
})
</script>

<template>
  <section class="analysis-task" aria-labelledby="backend-title">
    <h3 id="backend-title">徒手深蹲 · 后端分析</h3>
    <p>上传后使用真实深蹲算法分析，所选视频将保存到本机业务服务。</p>
    <div class="analysis-actions">
      <button @click="playReminder('请调整手机位置，确保全身进入画面。')">试听语音提醒</button>
      <button @click="stopReminder">停止语音</button>
    </div>
    <p role="status">{{ speechNote }}</p>
    <label
      >动作类型
      <select v-model="action" :disabled="busy">
        <option value="normal_squat">普通深蹲</option>
        <option value="wide_squat">宽站距深蹲</option>
        <option value="narrow_squat">窄站距深蹲</option>
      </select></label
    >
    <label
      >拍摄视角
      <select v-model="cameraView" :disabled="busy">
        <option value="unknown">不确定</option>
        <option value="front">正面</option>
        <option value="side">侧面</option>
        <option value="oblique">斜面</option>
      </select></label
    >
    <div class="analysis-actions">
      <button class="start-analysis-button backend-start" :disabled="!ready || busy" @click="start">
        {{
          task && !terminal(task) ? '继续查询任务' : videoId ? '提交深蹲任务' : '上传并创建深蹲任务'
        }}
      </button>
      <button class="cancel-analysis-button" :disabled="!canCancel" @click="query(begin(), true)">
        取消后端任务
      </button>
    </div>
    <p role="status" class="backend-status">{{ message }}</p>
    <dl class="analysis-task-meta">
      <div v-if="videoId">
        <dt>服务端视频编号</dt>
        <dd>{{ videoId }}</dd>
      </div>
      <div v-if="task">
        <dt>任务编号</dt>
        <dd>{{ task.task_id }}</dd>
      </div>
      <div v-if="task?.reason_code">
        <dt>原因代码</dt>
        <dd>{{ task.reason_code }}</dd>
      </div>
    </dl>
    <button class="reset-button" @click="loadHistory">查询本会话最近任务</button>
    <SquatFeedbackResults v-if="task?.algorithm" :task="task" @speak="playReminder" />
    <p role="status">{{ historyNote }}</p>
    <ul v-if="history?.length">
      <li v-for="row in history" :key="row.task_id">
        {{ row.task_id }} · {{ taskLabels[row.status] }} {{ row.reason_code }}
        <button v-if="row.status === 'COMPLETED'" :disabled="busy" @click="showHistoryResult(row)">
          查看练习反馈
        </button>
      </li>
    </ul>
  </section>
</template>
<style scoped>
.analysis-task {
  overflow-wrap: anywhere;
}
</style>
