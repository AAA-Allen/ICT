<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps<{ src: string; simulateFailure: boolean }>()
const player = ref<HTMLVideoElement | null>(null)
const status = ref<'loading' | 'ready' | 'failed'>('loading')
const message = ref('')
const attempt = ref(0)
let timer: number | null = null
let disposed = false

function clearTimer() {
  if (timer !== null) window.clearTimeout(timer)
  timer = null
}

function releaseMedia() {
  const media = player.value
  if (!media) return
  media.pause()
  media.removeAttribute('src')
  media.load()
}

function fail(reason: string) {
  clearTimer()
  releaseMedia()
  status.value = 'failed'
  message.value = reason
}

function retry() {
  clearTimer()
  releaseMedia()
  status.value = 'loading'
  message.value = ''
  attempt.value += 1
}

function isCurrent(event: Event) {
  return (
    !disposed &&
    event.currentTarget === player.value &&
    (event.currentTarget as HTMLVideoElement).dataset.attempt === String(attempt.value)
  )
}

function ready(event: Event) {
  if (!isCurrent(event) || status.value !== 'loading') return
  clearTimer()
  status.value = 'ready'
}

function mediaError(event: Event) {
  if (isCurrent(event)) fail('视频暂时无法解码或读取，请重试加载。')
}

watch(
  attempt,
  () => {
    clearTimer()
    timer = window.setTimeout(
      () => {
        timer = null
        fail(
          props.simulateFailure
            ? '这是主动选择的播放器故障样例。恢复“正常查询”后可重新播放。'
            : '视频加载等待超时，可以重试加载。',
        )
      },
      props.simulateFailure ? 350 : 8000,
    )
  },
  { immediate: true, flush: 'sync' },
)

onBeforeUnmount(() => {
  disposed = true
  clearTimer()
  releaseMedia()
})
</script>

<template>
  <div class="demonstration-playback" :data-status="status">
    <div class="demonstration-video-frame" :aria-busy="status === 'loading'">
      <video
        v-if="status !== 'failed' && !simulateFailure"
        :key="attempt"
        ref="player"
        class="demonstration-video"
        :src="src"
        :data-attempt="attempt"
        controls
        playsinline
        preload="auto"
        tabindex="0"
        aria-label="本地播放器测试片段，不包含动作教学"
        aria-describedby="demonstration-media-description"
        @loadeddata="ready"
        @canplay="ready"
        @error="mediaError"
      >
        当前浏览器无法播放视频，请使用支持 MP4 的浏览器。
      </video>
      <div v-if="status === 'loading'" class="demonstration-loading" role="status">
        正在加载本地测试片段…
      </div>
      <div v-else-if="status === 'failed'" class="demonstration-player-error" role="status">
        <strong>播放器加载失败{{ simulateFailure ? '（模拟）' : '' }}</strong>
        <p>{{ message }}</p>
        <button type="button" class="demonstration-retry-playback" @click="retry">重试加载</button>
      </div>
    </div>
    <p v-if="status === 'ready'" class="demonstration-ready-note" role="status">
      测试片段已就绪，点击播放；不自动播放，也不控制上方的练习视频。
    </p>
  </div>
</template>
