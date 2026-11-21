import framingUrl from '../assets/coach/framing.mp3'
import kneesUrl from '../assets/coach/knees.mp3'
import previewUrl from '../assets/coach/preview.mp3'
import rangeUrl from '../assets/coach/range.mp3'
import readyUrl from '../assets/coach/ready.mp3?no-inline'
import startUrl from '../assets/coach/start.mp3'

const cues = {
  preview: { url: previewUrl, cooldown: 0 },
  ready: { url: readyUrl, cooldown: 0 },
  start: { url: startUrl, cooldown: 0 },
  knees: { url: kneesUrl, cooldown: 1200 },
  range: { url: rangeUrl, cooldown: 1200 },
  framing: { url: framingUrl, cooldown: 3500 },
}

type Cue = keyof typeof cues

export function createLiveCoachAudio(onError: () => void) {
  let player: HTMLAudioElement | undefined
  let correctionClipsRequested = false
  let active: symbol | undefined
  const lastPlayed = new Map<Cue, number>()

  function getPlayer() {
    if (!player) {
      player = new Audio()
      player.preload = 'auto'
      player.src = startUrl
    }
    return player
  }

  function preload() {
    getPlayer()
    if (correctionClipsRequested) return
    correctionClipsRequested = true
    for (const url of [kneesUrl, rangeUrl]) {
      void fetch(url)
        .then((response) => response.arrayBuffer())
        .catch(() => undefined)
    }
  }

  function play(cue: Cue, interrupt = false) {
    const { url, cooldown } = cues[cue]
    const now = performance.now()
    if (now - (lastPlayed.get(cue) ?? -Infinity) < cooldown) return false
    if (active && !interrupt) return false

    // Keep one media element so taps can authorize its subsequent training cues.
    const audio = getPlayer()
    audio.pause()
    const playback = Symbol()
    active = playback
    audio.onended = () => {
      if (active === playback) active = undefined
    }
    const fail = () => {
      if (active !== playback) return
      active = undefined
      lastPlayed.delete(cue)
      onError()
    }
    audio.onerror = fail
    if (audio.getAttribute('src') === url) audio.currentTime = 0
    else audio.src = url

    lastPlayed.set(cue, now)
    void audio.play().catch(fail)
    return true
  }

  function stop() {
    active = undefined
    if (player) {
      player.onended = null
      player.onerror = null
      player.pause()
    }
    lastPlayed.clear()
  }

  return { preload, play, stop }
}
