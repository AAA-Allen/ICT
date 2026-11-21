import type { MuscleId } from '../types/muscle'
import type { BodyView, MuscleMapRegion } from '../types/muscleMap'

// 本项目原创的简化部位图。坐标属于 320 × 540 的 SVG 画布，不是医学解剖分区。
export const bodyOutline = `
  M145 75 L145 88 C130 90 111 94 102 103 C91 111 86 128 81 147
  L69 191 C63 207 58 229 54 249 L48 276
  C41 281 37 291 39 301 L44 310 C47 314 52 312 53 307 L59 290
  L65 285 L69 267 L84 225 L97 196 L111 151 L120 190 L125 218
  L119 245 C114 263 120 295 125 321 L132 365 L126 410 L132 462 L133 489
  L121 502 Q116 508 122 512 L149 514 C155 513 155 505 151 499
  L148 488 L151 436 L154 400 L153 363 L157 316 L160 279
  L163 316 L167 363 L166 400 L169 436 L172 488
  L169 499 C165 505 165 513 171 514 L198 512 Q204 508 199 502
  L187 489 L188 462 L194 410 L188 365 L195 321
  C200 295 206 263 201 245 L195 218 L200 190 L209 151
  L223 196 L236 225 L251 267 L255 285 L261 290 L267 307
  C268 312 273 314 276 310 L281 301 C283 291 279 281 272 276
  L266 249 C262 229 257 207 251 191 L239 147
  C234 128 229 111 218 103 C209 94 190 90 175 88 L175 75 Z
`

const deltoids: MuscleMapRegion = {
  id: 'deltoids',
  paths: ['M112 100 C98 104 92 117 88 138 L106 146 C111 131 115 119 123 110 Z'],
  label: { x: 310, y: 100, anchor: 'end', line: 'M266 96 H238 L218 116' },
}

const upperArm = ['M88 150 L106 156 L96 193 Q91 207 80 204 L78 198 Z']

// 小腿主要训练区域位于后侧，在背面展示，避免把胫骨前侧误标为腓肠肌。
export const muscleMapRegions: Record<BodyView, MuscleMapRegion[]> = {
  front: [
    {
      id: 'chest',
      paths: ['M121 114 C133 105 146 105 157 112 L157 143 C145 153 130 148 119 137 Z'],
      label: { x: 10, y: 116, anchor: 'start', line: 'M54 112 H83 L121 127' },
    },
    deltoids,
    {
      id: 'biceps',
      paths: upperArm,
      label: { x: 10, y: 193, anchor: 'start', line: 'M66 189 H73 L91 174' },
    },
    {
      id: 'abs',
      paths: [
        'M143 158 L157 158 L157 176 L142 176 Z',
        'M142 181 L157 181 L157 199 L142 199 Z',
        'M143 204 L157 204 L157 224 L146 223 Z',
        'M123 155 L136 160 L138 203 L143 231 L131 218 Z',
      ],
      label: { x: 310, y: 220, anchor: 'end', line: 'M279 216 H232 L179 188' },
    },
    {
      id: 'quads',
      paths: ['M126 259 C135 251 147 259 153 277 L151 317 L146 351 C137 354 134 341 131 323 Z'],
      label: { x: 310, y: 335, anchor: 'end', line: 'M253 331 H223 L182 313' },
    },
  ],
  back: [
    {
      id: 'trapezius',
      paths: ['M145 86 L157 94 L157 166 L145 151 L133 126 L117 110 Z'],
      label: { x: 10, y: 96, anchor: 'start', line: 'M54 92 H96 L145 111' },
    },
    deltoids,
    {
      id: 'lats',
      paths: ['M119 134 L135 143 L153 174 L151 213 L140 224 L127 199 Z'],
      label: { x: 10, y: 237, anchor: 'start', line: 'M54 233 H100 L139 190' },
    },
    {
      id: 'triceps',
      paths: upperArm,
      label: { x: 310, y: 185, anchor: 'end', line: 'M253 181 H244 L228 175' },
    },
    {
      id: 'glutes',
      paths: [
        'M128 232 C137 228 149 231 157 242 L157 269 C145 281 127 277 123 262 Q121 248 128 232 Z',
      ],
      label: { x: 310, y: 278, anchor: 'end', line: 'M279 274 H227 L181 256' },
    },
    {
      id: 'hamstrings',
      paths: ['M126 282 L151 282 L150 318 L145 352 L136 348 L129 315 Z'],
      label: { x: 10, y: 337, anchor: 'start', line: 'M54 333 H99 L140 316' },
    },
    {
      id: 'calves',
      paths: ['M137 376 C130 387 130 408 134 426 L140 448 L146 423 L148 395 L145 376 Z'],
      label: { x: 310, y: 421, anchor: 'end', line: 'M253 417 H217 L180 403' },
    },
  ],
}

export function isMuscleVisible(view: BodyView, muscleId: MuscleId): boolean {
  return muscleMapRegions[view].some((region) => region.id === muscleId)
}

export function preferredBodyView(muscleId: MuscleId): BodyView {
  return isMuscleVisible('front', muscleId) ? 'front' : 'back'
}
