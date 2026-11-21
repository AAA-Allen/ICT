<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import AnalysisTaskPanel from './AnalysisTaskPanel.vue'
import SquatBackendPanel from './SquatBackendPanel.vue'
import { useVideoPreview } from '../composables/useVideoPreview'
import { MAX_VIDEO_SIZE_MB, VIDEO_ACCEPT } from '../services/videoValidation'
import type { Exercise } from '../types/exercise'
import type { VideoLocationRequest } from '../types/analysisResult'
import type { TrainingConfiguration } from '../types/training'
import type { CompletedPractice, PracticeAgainRequest } from '../types/practice'
import { formatVideoTime } from '../utils/videoTime'

const props = defineProps<{
  screen?: 'video' | 'analysis'
  exercise: Exercise | null
  configuration: TrainingConfiguration | null
  practiceRequest: PracticeAgainRequest | null
  practiceBaseId: string | null
}>()
const emit = defineEmits<{
  analysis: []
  preview: []
  completed: [practice: CompletedPractice]
  'busy-change': [busy: boolean]
  'review-history': []
}>()
const fileInput = ref<HTMLInputElement | null>(null)
const dragDepth = ref(0)
const isOnlineTrial = import.meta.env.MODE === 'online'
const backendAllowed =
  !isOnlineTrial &&
  ['http://127.0.0.1:5173', 'http://localhost:5173'].includes(window.location.origin)
const analysisMode = ref<'mock' | 'backend'>(backendAllowed ? 'backend' : 'mock')
const taskBusy = ref(false)
function setBusy(value: boolean) {
  taskBusy.value = value
  emit('busy-change', value)
}
watch(
  () => props.exercise?.id,
  () => {
    analysisMode.value = backendAllowed ? 'backend' : 'mock'
    setBusy(false)
  },
)
watch(analysisMode, () => clearLocation())
const {
  selection,
  videoElement,
  isReading,
  errorMessage,
  playbackFailed,
  acceptFiles,
  removeVideo,
  handlePlaybackError,
  updateDuration,
} = useVideoPreview(() => props.exercise?.id ?? null)

watch(
  () => props.practiceRequest,
  (request) => {
    if (request) {
      removeVideo()
      dragDepth.value = 0
    }
  },
  { flush: 'sync' },
)

const activeLocation = shallowRef<VideoLocationRequest | null>(null)
const locationMessage = ref('')
let pendingLocation: (VideoLocationRequest & { targetSeconds: number }) | null = null
let locationTimer: number | null = null

function clearLocation() {
  if (locationTimer !== null) window.clearTimeout(locationTimer)
  locationTimer = null
  pendingLocation = null
  activeLocation.value = null
  locationMessage.value = ''
}

function finishLocation(media: HTMLVideoElement) {
  const pending = pendingLocation
  if (
    !pending ||
    selection.value?.objectUrl !== pending.videoKey ||
    selection.value.exerciseId !== pending.exerciseId
  )
    return
  if (Math.abs(media.currentTime - pending.targetSeconds) > 0.08) return
  if (locationTimer !== null) window.clearTimeout(locationTimer)
  locationTimer = null
  activeLocation.value = pending
  pendingLocation = null
  locationMessage.value = `已定位至 ${formatVideoTime(media.currentTime * 1000)}，视频已暂停，可手动播放查看。`
}

function locateVideo(request: VideoLocationRequest) {
  const media = videoElement.value
  if (
    !selection.value ||
    !media ||
    isReading.value ||
    playbackFailed.value ||
    selection.value.objectUrl !== request.videoKey ||
    selection.value.exerciseId !== request.exerciseId
  )
    return
  emit('preview')
  nextTick(() => applyLocation(request))
}

function applyLocation(request: VideoLocationRequest) {
  const media = videoElement.value
  if (!media) return
  clearLocation()
  if (
    !Number.isFinite(request.startMs) ||
    !Number.isFinite(request.endMs) ||
    request.startMs < 0 ||
    request.endMs <= request.startMs ||
    !Number.isFinite(media.duration) ||
    media.duration <= 0 ||
    media.readyState < 1
  ) {
    locationMessage.value = '当前视频时段暂不可定位，请先确认视频可以正常播放。'
    return
  }
  const targetSeconds = Math.min(request.startMs / 1000, Math.max(0, media.duration - 0.001))
  pendingLocation = { ...request, targetSeconds }
  const requestedLocation = pendingLocation
  locationMessage.value = '正在定位示例时段…'
  locationTimer = window.setTimeout(() => {
    if (pendingLocation !== requestedLocation) return
    pendingLocation = null
    locationTimer = null
    locationMessage.value = '暂时无法定位该时段，请使用播放器时间轴查看。'
  }, 5000)
  try {
    media.pause()
    media.currentTime = targetSeconds
    media.scrollIntoView({ block: 'center' })
    media.focus({ preventScroll: true })
    if (!media.seeking) finishLocation(media)
  } catch {
    clearLocation()
    locationMessage.value = '暂时无法定位该时段，请使用播放器时间轴查看。'
  }
}

function handleVideoSeeked(event: Event) {
  if (event.currentTarget === videoElement.value && videoElement.value)
    finishLocation(videoElement.value)
}

watch(
  [() => selection.value?.objectUrl, () => props.exercise?.id, isReading, playbackFailed],
  clearLocation,
  { flush: 'sync' },
)
onBeforeUnmount(clearLocation)

const statusText = computed(() => {
  if (!props.exercise) return '等待选择动作'
  if (isReading.value) return '正在读取视频'
  if (playbackFailed.value) return '预览不可用'
  return selection.value ? '本地视频已就绪' : '等待选择视频'
})

function chooseFile() {
  fileInput.value?.click()
}

function setVideoElement(element: unknown) {
  videoElement.value = element instanceof HTMLVideoElement ? element : null
}

function handleFileChange(event: Event) {
  const input = event.currentTarget as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = '' // 同一个文件也可以再次选择。
  acceptFiles(files)
}

function handleDrop(event: DragEvent) {
  dragDepth.value = 0
  acceptFiles(Array.from(event.dataTransfer?.files ?? []))
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDuration(seconds: number | null) {
  if (seconds === null) return '时长暂不可读'
  const total = Math.round(seconds)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}
</script>

<template>
  <section class="video-uploader" aria-labelledby="video-upload-title">
    <div v-show="screen !== 'analysis'" class="video-entry">
      <div class="section-heading">
        <div>
          <p class="eyebrow section-eyebrow">CAPTURE YOUR PROGRESS</p>
          <h2 id="video-upload-title" tabindex="-1">记录你的动作</h2>
        </div>
        <span class="local-only-tag">本地预览</span>
      </div>
      <p class="section-description">
        {{
          exercise
            ? `当前动作：${exercise.name}。选择视频，先确认画面和动作是否对应。`
            : '先确认训练配置，并在上方选择一个动作，再添加视频。'
        }}
      </p>
      <p v-if="practiceBaseId" class="practice-again-notice" role="status">
        已沿用前次训练条件。请选择这次练习的视频；完成模拟后会自动选中前后两条摘要供比较。
      </p>

      <div class="video-workspace">
        <div class="video-file-area">
          <div
            class="video-dropzone"
            :class="{ 'is-dragging': dragDepth > 0 && exercise, 'is-disabled': !exercise }"
            @dragenter.prevent="dragDepth += 1"
            @dragover.prevent
            @dragleave.prevent="dragDepth = Math.max(0, dragDepth - 1)"
            @drop.prevent="handleDrop"
          >
            <svg viewBox="0 0 48 48" fill="none" aria-hidden="true" class="video-file-icon">
              <rect
                x="8"
                y="9"
                width="25"
                height="30"
                rx="5"
                stroke="currentColor"
                stroke-width="1.6"
              />
              <path
                d="m33 20 8-5v18l-8-5M20 19v10m-5-5h10"
                stroke="currentColor"
                stroke-width="1.6"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
            <p class="dropzone-title">{{ exercise ? '将视频拖到这里' : '先选择一个训练动作' }}</p>
            <p id="video-file-rules" class="video-file-rules">
              MP4 / WebM · 单个文件不超过 {{ MAX_VIDEO_SIZE_MB }} MB
            </p>
            <input
              id="training-video-file"
              ref="fileInput"
              type="file"
              :accept="VIDEO_ACCEPT"
              :disabled="!exercise"
              hidden
              aria-label="选择动作视频"
              @change="handleFileChange"
            />
            <button
              type="button"
              class="choose-video-button"
              :disabled="!exercise"
              aria-describedby="video-file-rules"
              @click="chooseFile"
            >
              {{ selection || isReading ? '选择其他视频' : '选择视频' }}
            </button>
            <p class="video-local-hint">
              {{
                isOnlineTrial
                  ? '视频只在当前设备上预览。'
                  : '选择文件只开启本地预览。徒手深蹲可另行选择上传到本机后端。'
              }}
            </p>
          </div>

          <div class="video-status-row">
            <p class="video-read-status" role="status" aria-live="polite">
              <span
                class="video-status-dot"
                :class="{ 'is-ready': selection && !isReading && !playbackFailed }"
              />{{ statusText }}
            </p>
            <button
              v-if="selection || isReading"
              type="button"
              class="reset-button remove-video-button"
              @click="removeVideo"
            >
              移除视频
            </button>
          </div>
          <p v-if="isReading && selection" class="video-pending-note">
            正在读取新视频，现有预览会保留到替换成功。
          </p>
          <p v-if="errorMessage" class="video-error" role="alert">{{ errorMessage }}</p>

          <dl v-if="selection" class="video-metadata">
            <div class="video-file-detail">
              <dt>文件名称</dt>
              <dd class="video-file-name">{{ selection.file.name }}</dd>
            </div>
            <div class="video-file-detail">
              <dt>文件大小</dt>
              <dd>{{ formatFileSize(selection.file.size) }}</dd>
            </div>
            <div>
              <dt>视频时长</dt>
              <dd>{{ formatDuration(selection.durationSeconds) }}</dd>
            </div>
            <div class="video-file-detail">
              <dt>画面尺寸</dt>
              <dd>{{ selection.width }} × {{ selection.height }}</dd>
            </div>
            <div>
              <dt>关联动作</dt>
              <dd>{{ exercise?.name }}</dd>
            </div>
          </dl>
        </div>

        <div class="video-preview-area">
          <video
            v-if="selection"
            :key="selection.objectUrl"
            :ref="setVideoElement"
            class="local-video-player"
            :src="selection.objectUrl"
            controls
            playsinline
            tabindex="0"
            preload="metadata"
            :aria-label="`${exercise?.name ?? '动作'}的本地视频预览`"
            @error="handlePlaybackError"
            @durationchange="updateDuration"
            @seeked="handleVideoSeeked"
          >
            浏览器无法播放此视频，请选择其他文件。
          </video>
          <div v-else class="video-preview-empty">
            <span class="video-preview-play" aria-hidden="true">▷</span>
            <p>{{ exercise ? '视频预览会显示在这里' : '选好动作，再记录这次练习' }}</p>
            <span>你可以播放、暂停，或拖动视频时间轴查看画面。</span>
          </div>
          <p class="video-preview-caption">
            {{
              selection
                ? '请核对视频内容与关联动作；刷新页面后需要重新选择文件。'
                : '选择可预览视频后，可以在下方体验本地模拟分析流程。'
            }}
          </p>
          <p v-if="locationMessage" class="video-location-message" role="status" aria-live="polite">
            {{ locationMessage }}
          </p>
        </div>
      </div>
      <button
        class="confirm-button"
        :disabled="!selection || isReading || playbackFailed"
        @click="emit('analysis')"
      >
        下一步 · 分析与反馈 →
      </button>
    </div>
    <div v-show="screen === 'analysis'">
      <button class="reset-button" @click="emit('preview')">回看 / 更换练习视频</button>
      <label v-if="!isOnlineTrial && exercise?.id === 'bodyweight_squat'" class="backend-mode">
        分析方式
        <select v-model="analysisMode" :disabled="taskBusy">
          <option value="mock">本地模拟</option>
          <option value="backend" :disabled="!backendAllowed">
            {{ backendAllowed ? '本机后端 · 徒手深蹲' : '本机后端 · 请从 localhost:5173 打开' }}
          </option>
        </select>
      </label>
      <SquatBackendPanel
        v-if="analysisMode === 'backend' && exercise?.id === 'bodyweight_squat'"
        :key="selection?.objectUrl ?? 'empty'"
        :file="selection?.file"
        :ready="!!selection && !isReading && !playbackFailed"
        @busy-change="setBusy"
      />
      <AnalysisTaskPanel
        v-else
        :video="selection"
        :exercise="exercise"
        :ready="!!selection && !isReading && !playbackFailed"
        :active-location="activeLocation"
        :configuration="configuration"
        @locate="locateVideo"
        @clear-location="clearLocation"
        @completed="emit('completed', $event)"
        @busy-change="setBusy"
        @review-history="emit('review-history')"
      />
    </div>
  </section>
</template>
<style scoped>
.backend-mode {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin-top: 1rem;
}
.backend-mode select {
  font: inherit;
  padding: 0.4rem;
}
</style>
