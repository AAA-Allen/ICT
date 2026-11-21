export interface PosePoint {
  x: number
  y: number
  z: number
  visibility?: number
}

type Side = '左' | '右'
export type Phase = '取景' | '站立' | '下蹲' | '起身'
export interface Repetition {
  descent: number
  ascent: number
  knees: Side[]
  kneeEvidence: boolean
}

export type SquatCorrection = 'knees' | 'range'

export interface SquatUpdate {
  repetition?: Repetition
  correction?: SquatCorrection
}

const minCountedFlexion = 60
const minPartialFlexion = 45
const kneeEvidenceMs = 160

const legs = [
  [23, 25, 27],
  [24, 26, 28],
] as const
const required = [0, 11, 12, 23, 24, 25, 26, 27, 28]

function flexion(a: PosePoint, b: PosePoint, c: PosePoint) {
  const u = [a.x - b.x, a.y - b.y, a.z - b.z]
  const v = [c.x - b.x, c.y - b.y, c.z - b.z]
  const span = Math.hypot(...u) * Math.hypot(...v)
  if (span < 1e-8) return NaN
  const cosine = u.reduce((sum, x, i) => sum + x * v[i]!, 0) / span
  return 180 - (Math.acos(Math.max(-1, Math.min(1, cosine))) * 180) / Math.PI
}

export function squatMeasurement(image: PosePoint[], world: PosePoint[]) {
  const visible = required.every((i) => {
    const point = image[i]
    return (
      point &&
      (point.visibility ?? 0) >= 0.65 &&
      point.x > 0.02 &&
      point.x < 0.98 &&
      point.y > 0.02 &&
      point.y < 0.98
    )
  })
  if (!visible || world.length < 29) return null
  const angles = legs.map(([hip, knee, ankle]) => flexion(world[hip]!, world[knee]!, world[ankle]!))
  if (!angles.every(Number.isFinite)) return null
  return { depth: (angles[0]! + angles[1]!) / 2, angles }
}

export class LiveSquat {
  phase: Phase = '取景'
  private depth = NaN
  private standingSince: number | null = null
  private lastFrame: number | null = null
  private start = 0
  private bottom = 0
  private peak = 0
  private returnSince: number | null = null
  private baseline: number[] | null = null
  private kneeSince: (number | null)[] = [null, null]
  private evidenceSince: number | null = null
  private kneeEvidence = false
  private knees = new Set<Side>()

  losePose() {
    this.phase = '取景'
    this.depth = NaN
    this.standingSince = null
    this.lastFrame = null
    this.baseline = null
    this.returnSince = null
    this.kneeSince = [null, null]
    this.evidenceSince = null
    this.kneeEvidence = false
    this.knees.clear()
  }

  update(now: number, image: PosePoint[], world: PosePoint[]): SquatUpdate | null {
    const measurement = squatMeasurement(image, world)
    if (!measurement) {
      this.losePose()
      return null
    }
    if (this.lastFrame !== null && now - this.lastFrame > 450) this.losePose()
    this.lastFrame = now
    this.depth = Number.isFinite(this.depth)
      ? this.depth + 0.45 * (measurement.depth - this.depth)
      : measurement.depth
    const hipSpan = Math.abs(image[23]!.x - image[24]!.x)
    const torso = Math.abs((image[11]!.y + image[12]!.y - image[23]!.y - image[24]!.y) / 2)
    const frontal =
      hipSpan > 0.045 && torso > 0.07 && Math.abs(image[23]!.y - image[24]!.y) < torso * 0.22
    const inward = frontal
      ? legs.map(([hip, knee, ankle], i) => {
          const direction = Math.sign(image[legs[1 - i]![0]]!.x - image[hip]!.x)
          return (
            (direction * (image[knee]!.x - (image[ankle]!.x * 0.65 + image[hip]!.x * 0.35))) /
            hipSpan
          )
        })
      : null
    if (this.phase === '取景') {
      this.standingSince = this.depth <= 27 ? (this.standingSince ?? now) : null
      if (this.standingSince !== null && now - this.standingSince >= 300) {
        this.phase = '站立'
        this.baseline = inward
      }
      return null
    }
    if (this.phase === '站立') {
      if (this.depth <= 27 && measurement.depth <= 27) this.baseline = inward
      if (this.depth >= 36) {
        this.phase = '下蹲'
        this.start = this.bottom = now
        this.peak = this.depth
        this.returnSince = null
        this.kneeSince = [null, null]
        this.evidenceSince = null
        this.kneeEvidence = false
        this.knees.clear()
      }
      return null
    }
    if (now - this.start > 12000) {
      this.losePose()
      return null
    }
    if (this.depth > this.peak) {
      this.peak = this.depth
      this.bottom = now
    }
    const startedAscent = this.phase === '下蹲' && this.peak - this.depth >= 10
    if (startedAscent) this.phase = '起身'

    const hadKneeCorrection = this.knees.size > 0
    const observingKnees =
      this.depth >= 36 && (this.phase === '下蹲' || this.peak - this.depth <= 20)
    if (observingKnees && inward && this.baseline) {
      this.evidenceSince ??= now
      if (now - this.evidenceSince >= kneeEvidenceMs) this.kneeEvidence = true
      inward.forEach((value, i) => {
        const since = value - this.baseline![i]! >= 0.22 ? (this.kneeSince[i] ?? now) : null
        this.kneeSince[i] = since
        if (since !== null && now - since >= kneeEvidenceMs) this.knees.add(i === 0 ? '左' : '右')
      })
    } else {
      this.kneeSince = [null, null]
      this.evidenceSince = null
    }
    let correction: SquatCorrection | undefined
    if (!hadKneeCorrection && this.knees.size > 0) {
      correction = 'knees'
    } else if (
      startedAscent &&
      this.knees.size === 0 &&
      now - this.start >= 250 &&
      this.peak >= minPartialFlexion &&
      this.peak < minCountedFlexion
    ) {
      correction = 'range'
    }
    this.returnSince = this.depth <= 27 ? (this.returnSince ?? now) : null
    if (this.returnSince === null || now - this.returnSince < 170) {
      return correction ? { correction } : null
    }
    const end = this.returnSince
    this.phase = '站立'
    if (
      this.peak < minCountedFlexion ||
      this.bottom - this.start < 250 ||
      end - this.bottom < 250
    ) {
      return null
    }
    return {
      repetition: {
        descent: (this.bottom - this.start) / 1000,
        ascent: (end - this.bottom) / 1000,
        knees: [...this.knees],
        kneeEvidence: this.kneeEvidence,
      },
    }
  }
}
