<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ExerciseBrowser from '../components/ExerciseBrowser.vue'
import MuscleDetails from '../components/MuscleDetails.vue'
import MuscleExplorer from '../components/MuscleExplorer.vue'
import MuscleSelector from '../components/MuscleSelector.vue'
import TrainingPreferencesForm from '../components/TrainingPreferencesForm.vue'
import TrainingSummary from '../components/TrainingSummary.vue'
import VideoUploader from '../components/VideoUploader.vue'
import TrainingHistory from '../components/TrainingHistory.vue'
import QuerySourcePanel from '../components/QuerySourcePanel.vue'
import { queryCatalog } from '../services/catalogSource'
import { usePracticeHistory } from '../composables/usePracticeHistory'
import { comparisonBlockReason } from '../services/practiceComparison'
import type {
  CompletedPractice,
  PracticeAgainRequest,
  PracticeComparisonPair,
} from '../types/practice'
import { isMuscleVisible, preferredBodyView } from '../data/muscleMap'
import { createDefaultPreferences } from '../data/trainingOptions'
import { getLocalExercises } from '../services/exerciseService'
import type { MuscleId } from '../types/muscle'
import type { BodyView } from '../types/muscleMap'
import type { TrainingConfiguration } from '../types/training'

// 父页面拥有唯一的选择状态，按钮高亮和右侧详情都由它决定。
const isOnlineTrial = import.meta.env.MODE === 'online'
const selectedMuscleId = ref<MuscleId | null>(null)
const bodyView = ref<BodyView>('front')
const focusRequest = ref(0)
const showMuscleExtras = ref(false)
const preferences = ref(createDefaultPreferences())
const isTrainingConfirmed = ref(false)
const selectedExerciseId = ref<string | null>(null)
const {
  records,
  storageWarning,
  message: historyMessage,
  add,
  remove,
  clear,
  persist,
} = usePracticeHistory()
const analysisBusy = ref(false)
const reviewingHistory = ref(false)
const practiceBaseId = ref<string | null>(null)
const practiceRequest = ref<PracticeAgainRequest | null>(null)
const comparisonPair = ref<PracticeComparisonPair | null>(null)
let nextPracticeSequence = 0
const muscles = computed(() => queryCatalog.value.muscles)

const selectedMuscle = computed(
  () => muscles.value.find((muscle) => muscle.id === selectedMuscleId.value) ?? null,
)

// 仅在选中肌群时组合出完整配置；它不是已经约定好的后端请求。
const trainingConfiguration = computed<TrainingConfiguration | null>(() =>
  selectedMuscleId.value ? { muscleId: selectedMuscleId.value, ...preferences.value } : null,
)

// 动作选择由动作卡片与视频区域共用，因此状态提升到它们的共同父页面。
const selectedExercise = computed(() =>
  isTrainingConfirmed.value && trainingConfiguration.value
    ? (getLocalExercises(trainingConfiguration.value).find(
        (exercise) => exercise.id === selectedExerciseId.value,
      ) ?? null)
    : null,
)
const route = useRoute()
const router = useRouter()
const pages = [
  { id: 'muscles', title: '选择目标肌群', hint: '从身体出发，找到这次训练的重点' },
  { id: 'preferences', title: '设置训练条件', hint: '选择适合你的目标、经验与器械' },
  { id: 'exercises', title: '选择训练动作', hint: '为这一次练习选好动作' },
  { id: 'video', title: '记录动作视频', hint: '添加视频，确认画面与动作对应' },
  { id: 'analysis', title: '分析与反馈', hint: '查看任务进度、结果与纠错资料' },
  { id: 'history', title: '记录与比较', hint: '回看练习摘要，继续下一次练习' },
  { id: 'source', title: '资料来源', hint: '管理本地资料与 Java 查询快照' },
] as const
type Page = (typeof pages)[number]['id']
const page = computed(() => pages.find((item) => item.id === route.query.page)?.id ?? 'muscles')
const pageInfo = computed(() => pages.find((item) => item.id === page.value)!)
const initialPosition = Number(window.history.state?.position ?? 0)
function available(id: Page) {
  if (id === 'source') return !isOnlineTrial
  if (id === 'preferences') return !!selectedMuscleId.value
  if (id === 'exercises') return isTrainingConfirmed.value
  if (id === 'video' || id === 'analysis') return !!selectedExercise.value
  return true
}
function navigate(id: Page) {
  if (available(id) && page.value !== id) void router.push({ query: { page: id } })
}
function goBack() {
  if (Number(window.history.state?.position ?? 0) > initialPosition) router.back()
  else navigate('muscles')
}
watch(
  [page, selectedMuscleId, isTrainingConfirmed, selectedExercise],
  async () => {
    if (!available(page.value)) {
      await router.replace({ query: { page: selectedMuscleId.value ? 'preferences' : 'muscles' } })
    }
  },
  { immediate: true },
)
watch(page, async () => {
  await nextTick()
  window.scrollTo(0, 0)
  document.getElementById('page-title')?.focus({ preventScroll: true })
})
// 任何输入变化都取消旧确认，避免新条件仍显示为已确认。
watch([selectedMuscleId, preferences], () => {
  isTrainingConfirmed.value = false
  selectedExerciseId.value = null
  practiceBaseId.value = null
  reviewingHistory.value = false
})
watch(selectedExerciseId, () => {
  practiceBaseId.value = null
})
watch(records, (next) => {
  if (!next.some((record) => record.id === practiceBaseId.value)) practiceBaseId.value = null
})

function recordCompleted(practice: CompletedPractice) {
  const previousId = practiceBaseId.value
  const record = add(practice, previousId)
  if (!record) return
  practiceBaseId.value = null
  const previous =
    records.value.find((item) => item.id === previousId) ??
    records.value.slice(1).find((item) => !comparisonBlockReason(item, record))
  if (previous) comparisonPair.value = { beforeId: previous.id, afterId: record.id }
}

function reviewHistory() {
  reviewingHistory.value = true
  navigate('history')
}

async function practiceAgain(recordId: string) {
  if (analysisBusy.value) return
  const record = records.value.find((item) => item.id === recordId)
  if (
    !record ||
    !getLocalExercises(record.configuration).some((item) => item.id === record.result.exerciseId)
  )
    return
  const config = record.configuration
  selectedMuscleId.value = config.muscleId
  preferences.value = { goal: config.goal, level: config.level, equipment: config.equipment }
  bodyView.value = preferredBodyView(config.muscleId)
  // 先让条件变化的观察器完成清理，再恢复该记录已确认的动作。
  await nextTick()
  isTrainingConfirmed.value = true
  selectedExerciseId.value = record.result.exerciseId
  await nextTick()
  practiceBaseId.value = record.id
  practiceRequest.value = { recordId, sequence: ++nextPracticeSequence }
  reviewingHistory.value = false
  navigate('video')
  await nextTick()
  const choose = document.querySelector<HTMLButtonElement>('.choose-video-button')
  choose?.scrollIntoView({ block: 'center' })
  choose?.focus({ preventScroll: true })
}

function selectMuscle(muscleId: MuscleId) {
  selectedMuscleId.value = muscleId
  // 按钮与人体图共用此入口。即使重复选择同一部位，也能切回可见的视图。
  if (!isMuscleVisible(bodyView.value, muscleId)) {
    bodyView.value = preferredBodyView(muscleId)
  }
}

function clearSelection() {
  selectedMuscleId.value = null
}

function selectShortcut(muscleId: MuscleId) {
  selectMuscle(muscleId)
  focusRequest.value += 1
}

function resetPreferences() {
  preferences.value = createDefaultPreferences()
}

function confirmTraining() {
  if (!trainingConfiguration.value) return
  isTrainingConfirmed.value = true
  navigate('exercises')
}
</script>

<template>
  <main id="main-content" class="main-content" tabindex="-1">
    <Teleport to="#project-header">
      <button type="button" class="mobile-header-back" aria-label="返回上一个界面" @click="goBack">
        ←
      </button>
    </Teleport>
    <div class="screen-toolbar">
      <button
        class="screen-back"
        :disabled="page === 'muscles' && Number($route.query.page ? 1 : 0) === 0"
        @click="goBack"
      >
        ← 返回
      </button>
      <button v-if="!isOnlineTrial" class="reset-button" @click="navigate('source')">
        资料来源
      </button>
    </div>
    <header class="screen-heading">
      <p class="eyebrow">YOUR BODY. YOUR PROGRESS.</p>
      <h1 id="page-title" tabindex="-1">{{ pageInfo.title }}</h1>
      <p>{{ pageInfo.hint }}</p>
    </header>
    <nav class="screen-navigation" aria-label="功能导航">
      <button
        v-for="item in pages.filter((p) => p.id !== 'source')"
        :key="item.id"
        :aria-current="page === item.id ? 'page' : undefined"
        :aria-label="item.title"
        :title="item.title"
        :disabled="!available(item.id)"
        @click="navigate(item.id)"
      >
        <span>{{ item.title }}</span>
      </button>
    </nav>
    <div v-if="!isOnlineTrial" v-show="page === 'source'"><QuerySourcePanel /></div>

    <section v-show="page === 'muscles'" class="workspace" aria-labelledby="muscle-selector-title">
      <div class="selector-area">
        <div class="section-heading">
          <div>
            <p class="eyebrow section-eyebrow">CHOOSE YOUR FOCUS</p>
            <h2 id="muscle-selector-title">选择你的目标肌群</h2>
          </div>
          <button
            class="reset-button"
            type="button"
            :disabled="!selectedMuscleId"
            @click="clearSelection"
          >
            <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <path
                d="M4 7a6 6 0 1 1-.2 5M4 3v4h4"
                stroke="currentColor"
                stroke-width="1.5"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
            重置选择
          </button>
        </div>
        <p class="section-description">点击人体着色区域或部位按钮，探索你的训练起点。</p>

        <div class="muscle-visual-picker">
          <MuscleExplorer
            v-model:view="bodyView"
            :muscles="muscles"
            :selected-id="selectedMuscleId"
            :focus-request="focusRequest"
            @select="selectMuscle"
          />
          <div
            class="muscle-picker-buttons"
            :class="{ 'mobile-extras-collapsed': !showMuscleExtras }"
          >
            <p class="muscle-button-heading">快捷选择 · {{ muscles.length }} 个肌群</p>
            <MuscleSelector
              :muscles="muscles"
              :selected-id="selectedMuscleId"
              @select="selectShortcut"
            />
          </div>
        </div>

        <div class="selector-footer">
          <span class="selection-summary"
            ><span class="summary-dot" :class="{ 'has-selection': selectedMuscleId }" />{{
              selectedMuscle ? `当前选择：${selectedMuscle.name}` : '当前选择：尚未选择'
            }}</span
          >
          <span class="single-select-hint">每次专注一个目标</span>
        </div>
      </div>

      <div class="muscle-extra-toggle">
        <button
          class="reset-button"
          :aria-expanded="showMuscleExtras"
          @click="showMuscleExtras = !showMuscleExtras"
        >
          {{ showMuscleExtras ? '收起快捷选择与肌群详情 ↑' : '快捷选择与肌群详情 ↓' }}
        </button>
      </div>
      <div class="muscle-detail-wrap" :class="{ 'mobile-extras-collapsed': !showMuscleExtras }">
        <MuscleDetails :muscle="selectedMuscle" />
      </div>
    </section>

    <section
      v-show="page === 'preferences'"
      class="training-workspace"
      aria-labelledby="training-config-title"
    >
      <TrainingPreferencesForm
        v-model="preferences"
        :muscle-name="selectedMuscle?.name ?? null"
        @confirm="confirmTraining"
        @reset="resetPreferences"
      />
      <TrainingSummary
        :muscle="selectedMuscle"
        :preferences="preferences"
        :confirmed="isTrainingConfirmed"
      />
    </section>

    <div v-show="page === 'exercises'">
      <ExerciseBrowser
        v-model:selected-exercise-id="selectedExerciseId"
        :configuration="trainingConfiguration"
        :confirmed="isTrainingConfirmed"
      />
    </div>
    <div v-show="page === 'video' || page === 'analysis'">
      <VideoUploader
        :screen="page === 'analysis' ? 'analysis' : 'video'"
        :exercise="selectedExercise"
        :configuration="trainingConfiguration"
        :practice-request="practiceRequest"
        :practice-base-id="practiceBaseId"
        @analysis="navigate('analysis')"
        @preview="navigate('video')"
        @completed="recordCompleted"
        @busy-change="analysisBusy = $event"
        @review-history="reviewHistory"
      />
    </div>
    <div v-show="page === 'history'">
      <TrainingHistory
        :records="records"
        :storage-warning="storageWarning"
        :message="historyMessage"
        :busy="analysisBusy"
        :comparison-pair="comparisonPair"
        @again="practiceAgain"
        @remove="remove"
        @clear="clear"
        @persist="persist"
        @review="reviewingHistory = true"
      />
    </div>
    <div v-if="page === 'muscles'" class="screen-next">
      <button class="confirm-button" :disabled="!selectedMuscleId" @click="navigate('preferences')">
        下一步 · 设置训练条件 →
      </button>
    </div>
    <div v-if="page === 'exercises'" class="screen-next">
      <button class="confirm-button" :disabled="!selectedExercise" @click="navigate('video')">
        下一步 · 记录视频 →
      </button>
    </div>
    <div class="page-note">
      <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <circle cx="10" cy="10" r="7" stroke="currentColor" stroke-width="1.4" />
        <path d="M10 9v5M10 6v.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
      </svg>
      <p v-if="isOnlineTrial">视频复盘为模拟体验。实时深蹲可通过摄像头计数并提供语音提示。</p>
      <p v-else>
        本机徒手深蹲默认使用真实后端分析，其他动作可体验本地模拟。刷新后需重新选择练习视频，本地模拟摘要可在记录区查看。
      </p>
    </div>
  </main>
</template>
