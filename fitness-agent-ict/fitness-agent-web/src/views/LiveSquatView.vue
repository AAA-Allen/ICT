<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { PoseLandmarker } from '@mediapipe/tasks-vision'
import { createLiveCoachAudio } from '../services/liveCoachAudio'
import {
  LiveSquat,
  type Phase,
  type Repetition,
  type SquatCorrection,
  type SquatUpdate,
} from '../services/liveSquat'

type Stage = 'prepare' | 'training' | 'summary'

const route = useRoute()
const router = useRouter()
const video = ref<HTMLVideoElement>()
const heading = ref<HTMLHeadingElement>()
const stage = ref<Stage>('prepare')
const busy = ref(false)
const cameraReady = ref(false)
const phase = ref<Phase>('取景')
const startDelay = ref(5)
const countdown = ref<number | null>(null)
const repetitions = ref<Repetition[]>([])
const last = computed(() => repetitions.value.at(-1))
const issue = ref('')
const voiceEnabled = ref(true)
const voiceIssue = ref('')
const latestCorrection = ref<SquatCorrection | null>(null)
const diagnostics = ref({ fps: 0, infer: 0 })
let stream: MediaStream | undefined
let model: PoseLandmarker | undefined
let loop = 0
let generation = 0
let countdownTimer: number | undefined
const tracker = new LiveSquat()
const voice = createLiveCoachAudio(() => {
  voiceIssue.value = '语音播放失败，请点「试听」重试'
})
let framingSince: number | null = null
let framingAnnounced = false
let correctionUntil = 0

const training = computed(() => stage.value === 'training')
const finished = computed(() => stage.value === 'summary')
const canTrain = computed(() => cameraReady.value && phase.value === '站立')

function stagePath(value: Stage) {
  return { name: 'live', params: { step: value === 'prepare' ? '' : value } }
}

watch(
  () => route.params.step,
  (step) => {
    if (route.name === 'live' && (step || 'prepare') !== stage.value) {
      void router.replace(stagePath(stage.value))
    }
  },
  { immediate: true },
)

async function showStage(value: Stage) {
  stage.value = value
  await router.replace(stagePath(value))
  await nextTick()
  heading.value?.focus({ preventScroll: true })
}

function kneeFeedback(rep: Repetition) {
  if (rep.knees.length)
    return rep.knees.length === 2 ? '双膝有向内移动迹象' : `${rep.knees[0]}膝有向内移动迹象`
  return rep.kneeEvidence ? '未达到膝内移提醒条件' : '膝内移证据不足'
}

const instruction = computed(() => {
  if (busy.value) return '正在开启摄像头…'
  if (phase.value === '取景') return '站直，让头和双脚入镜'
  if (latestCorrection.value === 'knees') return '膝盖有内移迹象，注意让膝盖对准脚尖'
  if (latestCorrection.value === 'range') return '下蹲幅度偏小'
  if (last.value && phase.value === '站立') return kneeFeedback(last.value)
  if (phase.value === '起身') return '平稳起身，回到站立'
  return phase.value === '站立' ? '准备好就开始下蹲' : '下蹲中，保持动作平稳'
})

const preparationInstruction = computed(() => {
  if (busy.value) return '正在开启摄像头…'
  if (countdown.value === 0) return '倒计时结束，等你站直并全身入镜后开始'
  if (countdown.value !== null) return '请到镜头前站直，让头和双脚入镜'
  if (canTrain.value) return '取景就绪，可以开始'
  return startDelay.value > 0 ? '可以先点开始训练，再走到镜头前' : '请站直，让头和双脚入镜'
})

function stopVoice() {
  voiceIssue.value = ''
  framingSince = null
  framingAnnounced = false
  voice.stop()
}

function previewVoice() {
  voiceIssue.value = ''
  voice.play('preview', true)
}

function toggleVoice() {
  voiceEnabled.value = !voiceEnabled.value
  if (voiceEnabled.value) previewVoice()
  else stopVoice()
}

function updatePhase(now: number, update: SquatUpdate | null = null) {
  phase.value = tracker.phase
  if (!training.value) {
    if (countdown.value === 0 && canTrain.value) beginTraining()
    return
  }
  if (update?.repetition) repetitions.value.push(update.repetition)
  if (phase.value === '取景' || now >= correctionUntil) latestCorrection.value = null
  const correction = update?.correction
  if (correction) {
    latestCorrection.value = correction
    correctionUntil = now + 3500
  }
  if (!voiceEnabled.value) return
  if (phase.value === '取景') {
    framingSince ??= now
    if (!framingAnnounced && now - framingSince >= 700) {
      framingAnnounced = voice.play('framing')
    }
    return
  }
  framingSince = null
  framingAnnounced = false
  if (correction) {
    voiceIssue.value = ''
    voice.play(correction, true)
  }
}

function cancelStart() {
  window.clearTimeout(countdownTimer)
  countdownTimer = undefined
  countdown.value = null
  stopVoice()
}

function stop() {
  generation++
  cancelStart()
  cancelAnimationFrame(loop)
  stream?.getTracks().forEach((track) => track.stop())
  stream = undefined
  if (video.value) video.value.srcObject = null
  model?.close()
  model = undefined
  tracker.losePose()
  phase.value = '取景'
  latestCorrection.value = null
  cameraReady.value = busy.value = false
}

function requestStart() {
  if (!cameraReady.value || countdown.value !== null) return

  const delay = startDelay.value
  if (delay === 0) {
    beginTraining()
    return
  }

  const deadline = performance.now() + delay * 1000
  function tickCountdown() {
    const remaining = deadline - performance.now()
    countdown.value = Math.max(0, Math.ceil(remaining / 1000))
    countdownTimer =
      remaining > 0 ? window.setTimeout(tickCountdown, Math.min(remaining, 1000)) : undefined
  }
  tickCountdown()

  if (voiceEnabled.value) {
    voiceIssue.value = ''
    voice.play('ready', true)
  }
}

function beginTraining() {
  if (!canTrain.value) return
  countdown.value = null
  repetitions.value = []
  if (voiceEnabled.value) {
    voiceIssue.value = ''
    voice.play('start', true)
  }
  void showStage('training')
}

function finishTraining() {
  stop()
  void showStage('summary')
}

function prepareAgain() {
  repetitions.value = []
  issue.value = ''
  void showStage('prepare')
}

async function openCamera() {
  if (busy.value || cameraReady.value) return
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    issue.value = '当前地址无法访问摄像头，请在浏览器中打开可访问摄像头的 HTTPS 页面。'
    return
  }
  if (voiceEnabled.value) voice.preload()
  const token = ++generation
  busy.value = true
  issue.value = ''
  repetitions.value = []
  diagnostics.value = { fps: 0, infer: 0 }
  try {
    const camera = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: {
        facingMode: 'user',
        width: { ideal: 640 },
        height: { ideal: 480 },
        frameRate: { ideal: 24 },
      },
    })
    if (token !== generation) {
      camera.getTracks().forEach((track) => track.stop())
      return
    }
    stream = camera
    const element = video.value!
    element.srcObject = camera
    await element.play()
    const { FilesetResolver, PoseLandmarker } = await import('@mediapipe/tasks-vision')
    const assets = `${import.meta.env.BASE_URL}live-assets`
    const files = await FilesetResolver.forVisionTasks(assets)
    if (token !== generation) return
    const detector = await PoseLandmarker.createFromOptions(files, {
      baseOptions: { modelAssetPath: `${assets}/pose_landmarker_lite.task`, delegate: 'CPU' },
      runningMode: 'VIDEO',
      numPoses: 1,
    })
    if (token !== generation) {
      detector.close()
      return
    }
    model = detector
    busy.value = false
    cameraReady.value = true
    let previous = -Infinity
    let videoTime = -1
    let started = performance.now()
    let frames = 0
    let inferenceTime = 0
    const tick = (now: number) => {
      if (token !== generation) return
      try {
        if (now - previous >= 95 && element.readyState >= 2 && element.currentTime !== videoTime) {
          previous = now
          videoTime = element.currentTime
          const before = performance.now()
          const pose = detector.detectForVideo(element, now)
          const update = tracker.update(now, pose.landmarks[0] ?? [], pose.worldLandmarks[0] ?? [])
          updatePhase(now, update)
          frames++
          inferenceTime += performance.now() - before
          if (now - started >= 1000) {
            diagnostics.value = {
              fps: Math.round((1000 * frames) / (now - started)),
              infer: Math.round(inferenceTime / frames),
            }
            started = now
            frames = inferenceTime = 0
          }
        } else if (now - previous > 500 && Number.isFinite(previous)) {
          tracker.losePose()
          updatePhase(now)
        }
        loop = requestAnimationFrame(tick)
      } catch (error) {
        console.error(error)
        stop()
        issue.value = '姿态识别已中断，请重新开启摄像头。'
        if (training.value) void showStage('summary')
      }
    }
    loop = requestAnimationFrame(tick)
  } catch (error) {
    if (token !== generation) return
    console.error(error)
    stop()
    issue.value =
      error instanceof DOMException && error.name === 'NotAllowedError'
        ? '摄像头权限未开启，请在浏览器设置中允许摄像头。'
        : '无法启动摄像头或加载姿态模型，请检查设备和测试服务后重试。'
  }
}

onBeforeUnmount(stop)
</script>

<template>
  <main
    id="main-content"
    tabindex="-1"
    class="live"
    :class="{ 'is-training': training, 'is-summary': finished }"
  >
    <header class="live-header">
      <RouterLink class="back" to="/" aria-label="返回项目首页">←</RouterLink>
      <h1 ref="heading" tabindex="-1">
        {{ finished ? '训练总结' : training ? '实时深蹲' : '训练准备' }}
      </h1>
      <span v-if="training" class="live-status"><i aria-hidden="true" />训练中</span>
    </header>

    <div v-show="!finished" class="preview" :class="{ 'preview-ready': cameraReady || busy }">
      <video ref="video" aria-label="摄像头预览" autoplay muted playsinline />
      <div v-if="!cameraReady && !busy && !finished" class="framing">
        <span class="framing-mark" aria-hidden="true" />
        <strong>全身入镜</strong>
        <span>固定手机，正对镜头站直</span>
      </div>
      <div v-else-if="!training || phase === '取景'" class="frame-outline" aria-hidden="true" />
      <div
        v-if="countdown !== null && countdown > 0"
        class="countdown-badge"
        role="timer"
        :aria-label="'距开始还有 ' + countdown + ' 秒'"
      >
        {{ countdown }}
      </div>
    </div>

    <template v-if="training">
      <section class="scoreboard" aria-label="训练进度">
        <div class="count-tile">
          <span>完成次数</span>
          <strong data-testid="rep-count">{{ repetitions.length }}</strong>
        </div>
        <div class="phase-tile">
          <span>当前动作</span>
          <strong>{{ phase }}</strong>
        </div>
      </section>
      <p class="instruction" :class="{ 'is-correction': latestCorrection }" role="status">
        {{ instruction }}
      </p>
    </template>

    <p v-if="!training && !finished && (busy || cameraReady)" class="instruction" role="status">
      {{ preparationInstruction }}
    </p>

    <template v-if="finished">
      <div class="summary-count">
        <span>本组完成</span>
        <strong>{{ repetitions.length }} <small>次</small></strong>
      </div>
      <section v-if="repetitions.length" class="summary-list" aria-labelledby="rep-title">
        <h2 id="rep-title">逐次记录</h2>
        <ol>
          <li v-for="(rep, index) in repetitions" :key="index">
            <strong>第 {{ index + 1 }} 次</strong>
            <div>下降 {{ rep.descent.toFixed(2) }} 秒 · 起身 {{ rep.ascent.toFixed(2) }} 秒</div>
            <span>{{ kneeFeedback(rep) }}</span>
          </li>
        </ol>
      </section>
      <p v-else class="summary-empty">这组未记录到完整深蹲。下次先站直，等系统识别到站立再开始。</p>
      <details v-if="diagnostics.fps" class="diagnostics">
        <summary>检测详情</summary>
        <p>处理 {{ diagnostics.fps }} 帧/秒 · 平均单帧 {{ diagnostics.infer }} 毫秒</p>
      </details>
    </template>

    <div v-if="!finished" class="coach-controls">
      <label v-if="!training && !busy && countdown === null" class="start-delay">
        开始延时
        <select v-model.number="startDelay">
          <option :value="0">立即</option>
          <option :value="5">5 秒</option>
          <option :value="10">10 秒</option>
        </select>
      </label>
      <button class="voice-toggle" type="button" :aria-pressed="voiceEnabled" @click="toggleVoice">
        语音提示 <span aria-hidden="true">{{ voiceEnabled ? '开' : '关' }}</span>
      </button>
      <button v-if="voiceEnabled" class="voice-preview" type="button" @click="previewVoice">
        试听
      </button>
      <p v-if="voiceIssue" class="coach-issue" role="status">{{ voiceIssue }}</p>
    </div>
    <p v-if="issue" class="error" role="alert">{{ issue }}</p>
    <div class="actions">
      <button v-if="training" class="stop" type="button" @click="finishTraining">结束本组</button>
      <button v-else-if="finished" class="start" type="button" @click="prepareAgain">
        再练一组
      </button>
      <button v-else-if="busy" class="stop" type="button" @click="stop">取消</button>
      <button v-else-if="countdown !== null" class="stop" type="button" @click="cancelStart">
        取消开始
      </button>
      <button
        v-else-if="cameraReady"
        class="start"
        type="button"
        :disabled="startDelay === 0 && !canTrain"
        @click="requestStart"
      >
        开始训练
      </button>
      <button v-else class="start" type="button" @click="openCamera">开启摄像头</button>
      <p v-if="!training && !busy && !finished && !cameraReady" class="privacy">
        画面只在当前设备上处理
      </p>
    </div>
  </main>
</template>

<style scoped>
.live {
  --surface: #152822;
  --line: #2a4238;
  --muted: #a9c0b4;
  --accent: #d7f49b;
  width: min(100%, 35rem);
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
  gap: clamp(0.6rem, 1.7dvh, 1rem);
  margin-inline: auto;
  padding: max(1rem, env(safe-area-inset-top)) 1rem max(1rem, env(safe-area-inset-bottom));
  background: #0c1915;
  color: #f3f8ee;
  color-scheme: dark;
}
.live-header {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  min-height: 2.75rem;
}
.live h1 {
  margin: 0;
  font-size: clamp(1.25rem, 4.5vw, 1.7rem);
}
.back {
  display: grid;
  place-items: center;
  width: 2.75rem;
  height: 2.75rem;
  border: 1px solid var(--line);
  border-radius: 0.85rem;
  color: inherit;
  font-size: 1.4rem;
  text-decoration: none;
}
.live-status {
  justify-self: end;
  display: flex;
  align-items: center;
  gap: 0.32rem;
  color: var(--accent);
  font-size: 0.8rem;
  white-space: nowrap;
}
.live-status i {
  width: 0.42rem;
  height: 0.42rem;
  border-radius: 50%;
  background: currentColor;
}
.preview {
  position: relative;
  isolation: isolate;
  flex: 1 1 auto;
  min-height: clamp(12rem, 48dvh, 26rem);
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 1.15rem;
  background: radial-gradient(ellipse at center, #1a3329, #101e19 74%);
}
.preview video {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: contain;
  transform: scaleX(-1);
}
.preview:not(.preview-ready) video {
  visibility: hidden;
}
.framing {
  position: absolute;
  inset: 0;
  display: grid;
  place-content: center;
  justify-items: center;
  gap: 0.5rem;
  text-align: center;
  padding: 1rem;
}
.framing-mark {
  width: 4.8rem;
  height: 6.25rem;
  margin-bottom: 0.55rem;
  border: 1px dashed #789479;
  border-radius: 0.8rem;
  background:
    radial-gradient(circle at 50% 29%, var(--accent) 6%, transparent 7%),
    radial-gradient(ellipse at 50% 60%, #94b86d 18%, transparent 20%);
}
.framing strong {
  font-size: 1.45rem;
}
.framing span:last-child {
  color: var(--muted);
}
.frame-outline {
  position: absolute;
  inset: 9% 22%;
  border: 1px dashed #d7f49b9e;
  border-radius: 0.85rem;
  pointer-events: none;
}
.is-training .preview {
  min-height: clamp(5rem, calc(100dvh - 22rem), 24rem);
  flex: 1 1 0;
}
.countdown-badge {
  position: absolute;
  inset-block-start: 0.75rem;
  inset-inline: 0;
  display: grid;
  place-items: center;
  width: 4rem;
  height: 4rem;
  margin-inline: auto;
  border-radius: 50%;
  background: var(--accent);
  color: #142314;
  font-size: 2.25rem;
  font-weight: 750;
  font-variant-numeric: tabular-nums;
  pointer-events: none;
}
.start-delay {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
}
.start-delay select {
  min-height: 2.75rem;
  padding-inline: 0.55rem;
  border: 1px solid var(--line);
  border-radius: 0.75rem;
  background: var(--surface);
  color: inherit;
  font: inherit;
}
.coach-controls {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.35rem 0.75rem;
}
.voice-toggle,
.voice-preview {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  min-height: 2.75rem;
  border: 1px solid var(--line);
  border-radius: 0.75rem;
  padding-inline: 0.75rem;
  background: var(--surface);
  color: inherit;
}
.voice-preview {
  background: transparent;
}
.voice-toggle span,
.coach-issue {
  color: var(--muted);
  font-size: 0.875rem;
}
.voice-toggle[aria-pressed='true'] span {
  color: var(--accent);
}
.scoreboard {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.65rem;
}
.scoreboard > div,
.summary-count {
  display: grid;
  justify-items: start;
  align-content: center;
  padding: 0.55rem 0.85rem;
  border-radius: 0.95rem;
}
.count-tile,
.summary-count {
  background: var(--accent);
  color: #142314;
}
.phase-tile {
  background: var(--surface);
  border: 1px solid var(--line);
}
.scoreboard span,
.summary-count span {
  font-size: 0.8rem;
  font-weight: 650;
}
.scoreboard strong,
.summary-count strong {
  font-size: clamp(2rem, 7.5dvh, 4.1rem);
  line-height: 1;
  font-variant-numeric: tabular-nums;
}
.phase-tile strong {
  font-size: clamp(1.5rem, 5dvh, 2.45rem);
}
.instruction {
  margin: 0;
  min-height: 2.25rem;
  display: grid;
  align-content: center;
  color: #e1eddb;
  font-size: clamp(0.98rem, 2.4dvh, 1.25rem);
  font-weight: 650;
}
.instruction.is-correction {
  color: #ffe1aa;
  font-weight: 650;
}
.actions {
  margin-top: auto;
}
.actions button {
  width: 100%;
  min-height: 2.9rem;
  border-radius: 0.85rem;
  font: inherit;
  font-weight: 750;
  cursor: pointer;
}
.start {
  background: var(--accent);
  border: 1px solid var(--accent);
  color: #142314;
}
.start:disabled {
  background: var(--surface);
  border-color: var(--line);
  color: var(--muted);
  cursor: not-allowed;
}
.stop {
  background: var(--surface);
  border: 1px solid #496257;
  color: inherit;
}
.privacy,
.diagnostics,
.summary-empty {
  color: var(--muted);
  font-size: 0.88rem;
}
.privacy {
  margin: 0.55rem 0 0;
  text-align: center;
}
.error {
  margin: 0;
  color: #ffc6a8;
}
.summary-count {
  min-height: 7rem;
}
.summary-count small {
  font-size: 1rem;
}
.summary-list h2 {
  font-size: 1.1rem;
  margin: 0 0 0.55rem;
}
.summary-list ol {
  list-style: none;
  padding: 0;
  margin: 0;
}
.summary-list li {
  padding: 0.65rem 0;
  border-top: 1px solid var(--line);
  font-variant-numeric: tabular-nums;
}
.summary-list li span {
  color: var(--muted);
  font-size: 0.85rem;
}
.diagnostics summary {
  cursor: pointer;
}
:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 3px;
}
@media (max-height: 560px) {
  .is-training {
    gap: 0.45rem;
    padding-block: 0.6rem;
  }
  .is-training .preview {
    min-height: clamp(3.5rem, calc(100dvh - 15rem), 14rem);
  }
  .scoreboard > div {
    padding-block: 0.35rem;
  }
}
</style>
