import { synthesizeSpeech } from './squatBackend'

/** Reusable playback for future camera events. Drops overlapping/duplicate reminders. */
export function createCoachSpeech() {
  const cache = new Map<string, string>()
  let controller: AbortController | null = null
  let audio: HTMLAudioElement | null = null
  let lastText = ''
  let lastTime = 0
  let generation = 0

  function stop() {
    generation++
    controller?.abort()
    controller = null
    audio?.pause()
    audio = null
  }

  async function speak(text: string) {
    text = text.trim()
    if (!text || text.length > 120) throw new Error('提醒文字需为 1–120 字')
    if (controller || audio || (text === lastText && Date.now() - lastTime < 8000)) return
    const current = generation
    const pending = new AbortController()
    controller = pending
    try {
      let url = cache.get(text)
      if (!url) {
        const result = await synthesizeSpeech(text, pending.signal)
        if (current !== generation) return
        url = `data:${result.mime_type};base64,${result.audio_base64}`
        if (cache.size >= 20) cache.delete(cache.keys().next().value!)
        cache.set(text, url)
      }
      if (current !== generation) return
      const player = new Audio(url)
      audio = player
      const clear = () => {
        if (audio === player) audio = null
      }
      player.onended = clear
      player.onerror = clear
      await player.play()
      lastText = text
      lastTime = Date.now()
    } catch (error) {
      if (current === generation) {
        audio = null
        throw error
      }
    } finally {
      if (controller === pending) controller = null
    }
  }
  return { speak, stop }
}
