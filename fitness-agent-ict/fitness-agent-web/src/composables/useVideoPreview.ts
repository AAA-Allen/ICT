import { onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { validateVideoFile } from '../services/videoValidation'
import type { LocalVideoSelection } from '../types/video'

interface PendingVideo {
  file: File
  objectUrl: string
  exerciseId: string
  media: HTMLVideoElement
  timer: number
}

export function useVideoPreview(getExerciseId: () => string | null) {
  const selection = shallowRef<LocalVideoSelection | null>(null)
  const videoElement = ref<HTMLVideoElement | null>(null)
  const isReading = ref(false)
  const errorMessage = ref('')
  const playbackFailed = ref(false)
  let pending: PendingVideo | null = null

  function detachMedia(media: HTMLVideoElement | null) {
    if (!media) return
    media.pause()
    media.removeAttribute('src')
    media.load()
  }

  function disposeProbe(probe: PendingVideo, revokeUrl: boolean) {
    window.clearTimeout(probe.timer)
    probe.media.onloadedmetadata = null
    probe.media.onerror = null
    detachMedia(probe.media)
    if (revokeUrl) URL.revokeObjectURL(probe.objectUrl)
  }

  function cancelPending() {
    const previous = pending
    pending = null
    if (previous) disposeProbe(previous, true)
    isReading.value = false
  }

  function clearCurrent() {
    const previous = selection.value
    selection.value = null
    playbackFailed.value = false
    detachMedia(videoElement.value)
    if (previous) URL.revokeObjectURL(previous.objectUrl)
  }

  function removeVideo() {
    cancelPending()
    clearCurrent()
    errorMessage.value = ''
  }

  function rejectProbe(probe: PendingVideo, message: string) {
    // 已被替换或取消的读取结果，不得覆盖当前视频。
    if (pending !== probe) return
    pending = null
    disposeProbe(probe, true)
    isReading.value = false
    errorMessage.value = message
  }

  function acceptFiles(files: File[]) {
    if (files.length === 0) return // 取消文件选择不改变已有预览。
    const exerciseId = getExerciseId()
    if (!exerciseId) {
      errorMessage.value = '请先选择一个训练动作，再添加视频。'
      return
    }

    // 新的一次选择会取消尚未完成的读取，但保留已经可用的预览。
    cancelPending()
    if (files.length !== 1) {
      errorMessage.value = '每次请选择一个视频文件。'
      return
    }
    const file = files[0]!
    const validationError = validateVideoFile(file)
    if (validationError) {
      errorMessage.value = validationError
      return
    }

    errorMessage.value = ''
    isReading.value = true
    let objectUrl: string
    try {
      objectUrl = URL.createObjectURL(file)
    } catch {
      isReading.value = false
      errorMessage.value = '暂时无法打开这个文件，请重新选择。'
      return
    }

    const media = document.createElement('video')
    media.preload = 'metadata'
    const probe: PendingVideo = { file, objectUrl, exerciseId, media, timer: 0 }
    pending = probe
    probe.timer = window.setTimeout(() => {
      rejectProbe(probe, '读取视频超时，请选择其他文件或重试。')
    }, 10000)
    media.onerror = () => rejectProbe(probe, '无法读取这个视频，文件可能损坏或编码不受浏览器支持。')
    media.onloadedmetadata = () => {
      if (pending !== probe || getExerciseId() !== probe.exerciseId) return
      if (media.videoWidth <= 0 || media.videoHeight <= 0) {
        rejectProbe(probe, '文件中没有可预览的视频画面，请选择其他视频。')
        return
      }
      const nextSelection: LocalVideoSelection = {
        file: probe.file,
        objectUrl: probe.objectUrl,
        exerciseId: probe.exerciseId,
        durationSeconds:
          Number.isFinite(media.duration) && media.duration > 0 ? media.duration : null,
        width: media.videoWidth,
        height: media.videoHeight,
      }
      pending = null
      // 新视频验证成功后才替换旧预览；失败不会丢掉原来的视频。
      disposeProbe(probe, false)
      clearCurrent()
      selection.value = nextSelection
      isReading.value = false
      errorMessage.value = ''
    }
    media.src = objectUrl
    media.load()
  }

  function handlePlaybackError(event: Event) {
    if (!selection.value || event.currentTarget !== videoElement.value) return
    playbackFailed.value = true
    errorMessage.value = '这个视频无法正常播放，请更换文件。'
  }

  function updateDuration(event: Event) {
    const media = event.currentTarget as HTMLVideoElement
    if (!selection.value || media !== videoElement.value) return
    if (Number.isFinite(media.duration) && media.duration > 0) {
      selection.value = { ...selection.value, durationSeconds: media.duration }
    }
  }

  watch(getExerciseId, removeVideo, { flush: 'sync' })
  onBeforeUnmount(removeVideo)

  return {
    selection,
    videoElement,
    isReading,
    errorMessage,
    playbackFailed,
    acceptFiles,
    removeVideo,
    handlePlaybackError,
    updateDuration,
  }
}
