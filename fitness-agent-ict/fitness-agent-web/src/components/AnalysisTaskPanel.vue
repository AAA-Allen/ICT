<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AnalysisResults from './AnalysisResults.vue'
import { useAnalysisTask } from '../composables/useAnalysisTask'
import { analysisScenarios, analysisStatusLabels } from '../data/analysisScenarios'
import type { AnalysisInput, MockAnalysisScenario } from '../types/analysis'
import type { Exercise } from '../types/exercise'
import type { LocalVideoSelection } from '../types/video'
import { resultPresets } from '../data/analysisResultOptions'
import { canLocateIssue } from '../utils/videoTime'
import type { MockResultPreset, VideoLocationRequest } from '../types/analysisResult'
import type { TrainingConfiguration } from '../types/training'
import type { CompletedPractice } from '../types/practice'

const props = defineProps<{
  video: LocalVideoSelection | null
  exercise: Exercise | null
  ready: boolean
  activeLocation: VideoLocationRequest | null
  configuration: TrainingConfiguration | null
}>()
const emit = defineEmits<{
  locate: [request: VideoLocationRequest]
  'clear-location': []
  completed: [practice: CompletedPractice]
  'busy-change': [busy: boolean]
  'review-history': []
}>()
const scenario = ref<MockAnalysisScenario>('success')
const resultPreset = ref<MockResultPreset>('with_issues')
const input = computed<AnalysisInput | null>(() => {
  if (
    !props.ready ||
    !props.video ||
    !props.exercise ||
    props.video.exerciseId !== props.exercise.id
  )
    return null
  return {
    videoKey: props.video.objectUrl,
    videoName: props.video.file.name,
    exerciseId: props.exercise.id,
    exerciseName: props.exercise.name,
    durationSeconds: props.video.durationSeconds,
  }
})
const { state, isBusy, start, cancel } = useAnalysisTask(() => input.value)
watch(isBusy, (busy) => emit('busy-change', busy), { immediate: true })
const statusLabel = computed(() =>
  input.value ? analysisStatusLabels[state.value.status] : '等待可预览视频',
)
const startLabel = computed(() => {
  if (isBusy.value) return '模拟进行中'
  if (state.value.status === 'idle') return '开始模拟分析'
  return state.value.status === 'completed' ? '重新模拟' : '重试模拟'
})
const taskScenarioLabel = computed(
  () => analysisScenarios.find((option) => option.value === state.value.task?.scenario)?.label,
)
const activeIssueId = computed(() =>
  props.activeLocation?.taskId === state.value.result?.taskId
    ? (props.activeLocation?.issueId ?? null)
    : null,
)

function locateIssue(issueId: string) {
  const { task, result, status } = state.value
  if (
    status !== 'completed' ||
    !task ||
    !result ||
    !input.value ||
    input.value.videoKey !== task.videoKey
  )
    return
  const issue = result.issues.find((candidate) => candidate.id === issueId)
  if (!issue || !canLocateIssue(issue, result.durationSeconds)) return
  emit('locate', {
    videoKey: task.videoKey,
    exerciseId: task.exerciseId,
    taskId: task.id,
    issueId: issue.id,
    startMs: issue.startMs,
    endMs: issue.endMs,
  })
}

watch(
  state,
  (next) => {
    if (next.status !== 'completed' || !next.result) emit('clear-location')
    if (
      next.status === 'completed' &&
      next.result &&
      next.task &&
      input.value &&
      next.task.videoKey === input.value.videoKey &&
      props.configuration
    )
      emit('completed', { result: next.result, configuration: { ...props.configuration } })
  },
  { flush: 'sync' },
)
</script>

<template>
  <section class="analysis-task" aria-labelledby="analysis-task-title" :data-status="state.status">
    <div class="analysis-heading">
      <div>
        <p class="eyebrow section-eyebrow">TRY THE ANALYSIS FLOW</p>
        <h3 id="analysis-task-title">本地模拟分析</h3>
      </div>
      <span class="analysis-mock-badge">模拟流程 · 未上传</span>
    </div>
    <p class="analysis-mode-description">
      用可复现的场景体验任务与结果展示。这里不识别视频内容，所有结果均为固定样例。
    </p>

    <div class="analysis-workspace">
      <div class="analysis-settings">
        <label for="analysis-scenario">下一次模拟场景</label>
        <select id="analysis-scenario" v-model="scenario" :disabled="isBusy">
          <option v-for="option in analysisScenarios" :key="option.value" :value="option.value">
            {{ option.label }}
          </option>
        </select>
        <div v-if="scenario === 'success'" class="result-preset-field">
          <label for="analysis-result-preset">下一次结果样例</label>
          <select id="analysis-result-preset" v-model="resultPreset" :disabled="isBusy">
            <option v-for="preset in resultPresets" :key="preset.value" :value="preset.value">
              {{ preset.label }}
            </option>
          </select>
        </div>
        <p class="analysis-scenario-hint">
          {{
            input
              ? '失败后可改选“正常完成”再重试。选择场景不会改变视频。'
              : '先在上方添加能正常预览的视频；读取或替换视频期间不能开始任务。'
          }}
        </p>
        <div class="analysis-actions">
          <button
            type="button"
            class="start-analysis-button"
            :disabled="!input || isBusy"
            @click="start(scenario, resultPreset)"
          >
            {{ startLabel }}
          </button>
          <button type="button" class="cancel-analysis-button" :disabled="!isBusy" @click="cancel">
            取消本次模拟
          </button>
        </div>

        <dl v-if="state.task" class="analysis-task-meta">
          <div>
            <dt>模拟编号</dt>
            <dd class="analysis-task-id">{{ state.task.id }}</dd>
          </div>
          <div>
            <dt>尝试次数</dt>
            <dd class="analysis-attempt">第 {{ state.task.attempt }} 次</dd>
          </div>
          <div>
            <dt>关联动作</dt>
            <dd>{{ state.task.exerciseName }}</dd>
          </div>
          <div>
            <dt>输入文件</dt>
            <dd class="analysis-file-name">{{ state.task.videoName }}</dd>
          </div>
          <div>
            <dt>本次场景</dt>
            <dd>{{ taskScenarioLabel }}</dd>
          </div>
        </dl>
      </div>

      <div class="analysis-state-panel" :class="`analysis-state-${state.status}`">
        <div role="status" aria-live="polite" aria-atomic="true">
          <p class="analysis-status-label">{{ statusLabel }}</p>
          <p class="analysis-status-message">
            {{ input ? state.message : '当前没有可用于任务的视频。先选择动作和视频，再开始模拟。' }}
          </p>
        </div>
        <progress v-if="isBusy" class="analysis-progress" aria-label="本地模拟任务正在进行" />
        <p v-if="state.status === 'completed'" class="analysis-outcome-note">
          下方已返回结构化固定样例，可定位回看。摘要已加入本地记录，可沿用条件再次练习。
        </p>
        <button
          v-if="state.status === 'completed'"
          type="button"
          class="review-history-button"
          @click="emit('review-history')"
        >
          查看本地记录与比较 ↓
        </button>
        <p v-else-if="state.status === 'insufficient_quality'" class="analysis-outcome-note">
          此分支不显示评分或纠错。可以更换视频后再试，或改选场景检查其他状态。
        </p>
        <p
          v-else-if="state.status === 'failed' || state.status === 'timed_out'"
          class="analysis-outcome-note"
        >
          可保留当前视频重试，也可替换视频后重新开始。
        </p>
      </div>
    </div>
    <AnalysisResults
      v-if="state.status === 'completed' && state.result && input"
      :result="state.result"
      :active-issue-id="activeIssueId"
      @locate="locateIssue"
    />
  </section>
</template>
