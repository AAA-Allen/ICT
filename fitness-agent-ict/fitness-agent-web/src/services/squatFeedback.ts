import type { SquatTask } from './squatBackend'

export const observationNames: Record<string, string> = {
  knee_inward: '膝盖疑似向内移动',
  heel_raise: '脚跟疑似抬起',
  forefoot_raise: '前脚掌疑似抬起',
  medial_foot_raise: '足内侧疑似抬起',
  lateral_foot_raise: '足外侧疑似抬起',
  back_rounding: '腰背姿态变化',
  lumbar_overextension: '腰部后伸',
  torso_coordination: '髋部与躯干起身可能不同步',
  lateral_shift: '身体疑似向一侧偏移',
  balance: '动作稳定性需要复查',
  plie: '观察到脚尖外开特征',
  hold: '观察到最低点停留',
}
export const guidanceSources = [
  {
    title: 'ACE · 自重深蹲动作指导',
    url: 'https://www.acefitness.org/resources/everyone/exercise-library/135/bodyweight-squat/',
  },
  {
    title: 'Mayo Clinic · 深蹲与个人能力范围',
    url: 'https://www.mayoclinic.org/healthy-lifestyle/fitness/multimedia/squat/vid-20084663',
  },
]
type Guidance = { mechanics: string; advice: string; metricKeys?: string[] }
const guidance: Record<string, Guidance> = {
  knee_inward: {
    mechanics:
      '深蹲需要髋、膝、踝协调运动。动态膝内移值得结合足部稳定与动作控制复查；宽窄站距、透视和站立时的腿形都会影响画面，二维内移不能直接换算成关节受力或损伤。',
    advice:
      '先减少下蹲深度、放慢动作，让膝盖大致跟随脚尖方向；起身初段保持双脚稳定推地，不必拼命向外顶膝。调整后用同一机位再录一组。',
  },
  heel_raise: {
    mechanics:
      '在本次自重、全脚掌支撑的训练目标下，脚跟抬起可能改变支撑方式。单凭这个表现不能确定踝活动度不足或哪块肌肉代偿，需要先核对足部与地面。',
    advice:
      '先回看标记片段，确认脚跟确实离开支撑面。尝试减小下蹲深度、调整舒适站距，窄距下难以保持稳定时可稍微加宽。如果仍控制不住脚跟抬起，可在教练指导下，将稳定、防滑、左右等高的专用深蹲楔形垫放在双脚脚跟下方，保持前脚掌稳定接触地面，从较浅的自重深蹲重新尝试。不要随手叠放可能滑动的物品；出现疼痛或仍不稳时停止本次尝试。',
  },
  forefoot_raise: {
    mechanics:
      '前脚掌离地会改变原有足部支撑。骨架只能提示位置变化，实际地面接触和压力分布仍需查看画面。',
    advice:
      '减少向后坐的幅度，保持脚跟和前脚掌共同接触地面，在能站稳的深度内练习，不刻意把全部重量压到脚跟。',
  },
  medial_foot_raise: {
    mechanics:
      '足内侧离地需要查看鞋底内缘与地面的接触；若确实发生，说明原有支撑方式发生变化，不能由此确定足底压力或关节负荷。',
    advice: '减少刻意外推膝盖的力度，调整到舒适站距和脚尖方向，先在较浅范围维持脚掌稳定。',
  },
  lateral_foot_raise: {
    mechanics: '足外侧离地需要清楚的鞋底外缘证据，现有关键点不能直接测量足底压力。',
    advice: '先减小深度，重新站稳，保持脚跟与前脚掌稳定接触，避免脚掌明显向内翻滚。',
  },
  torso_coordination: {
    mechanics:
      '起身需要伸髋与伸膝协调进行。若臀部先明显抬起而胸口滞后，躯干倾角可能发生变化；自然前倾本身不等于错误。',
    advice: '先减少深度与次数，起身时让髋部与胸口协调上升，保持腹部张力，避免突然把臀部猛抬起来。',
    metricKeys: ['trunk_image_tilt_deg'],
  },
  back_rounding: {
    mechanics: '肩髋连线不能代表脊柱各节曲度，普通骨架不足以确认弓背，需结合清晰侧面画面复查。',
    advice: '保持自然脊柱姿态和腹部张力，在仍能控制姿态的深度结束下蹲，不为追求深度而强行下压。',
  },
  lumbar_overextension: {
    mechanics: '普通人体骨架缺少腰椎分段点，不能仅凭躯干倾斜确认腰椎过伸。',
    advice: '避免刻意过度挺腰，让胸廓与骨盆保持舒适协调，减少深度后再检查动作。',
  },
  lateral_shift: {
    mechanics:
      '身体侧偏提示需要复查左右协调与支撑，但相机转动、人物转向也可能造成投影偏移，不能直接认定某侧力量不足。',
    advice:
      '先站稳并减小深度，尽量平稳地下蹲和起身；若持续偏向一侧或伴随疼痛，请让专业人员现场评估。',
  },
  balance: {
    mechanics:
      '晃动或调整步可能提示当前动作难以稳定完成；主动挪脚和拍摄位移也需排除。视频不能直接测量重心或足底受力。',
    advice: '暂停并重新站稳，减少深度、放慢动作；若越做越难控制，结束本组并休息后再练。',
  },
  plie: {
    mechanics:
      '脚尖适当外转可以是正常深蹲站姿的一部分，需要结合所选变式、个人舒适度及膝盖方向判断，不以外开本身判错。',
    advice:
      '选择髋部舒适、脚掌稳定的站距和脚尖方向，让膝盖大致跟随脚尖；宽距时不强行将脚尖完全朝前，也不刻意追求更大外转。',
  },
  hold: {
    mechanics: '最低点停留是一种节奏安排，不自动构成问题；是否调整取决于训练目标及能否稳定控制。',
    advice:
      '若是计划内停顿，保持脚掌稳定与自然呼吸后平稳起身；若因失去控制而停住，先减少深度或次数。',
  },
}
export interface FeedbackCard {
  id: string
  problem: string
  label: string
  reason: string
  advice: string
}
// Old stored results can still expose measured kinematics without rerunning pose.
export function kinematicFeedback(task: SquatTask): FeedbackCard[] {
  const bio = task.algorithm?.results?.biomechanics
  if (bio?.dual_analysis) return bio.dual_analysis.kinematics.findings
  const metrics = bio?.metrics ?? {}
  const cards: FeedbackCard[] = []
  const valid = (key: string) => {
    const m = metrics[key]
    return m?.status === 'estimated' && (m.coverage == null || m.coverage >= 0.5) ? m : undefined
  }
  const knees = ['left', 'right'].flatMap((side) => {
    const m = valid(side + '_knee_flexion_deg')
    return m?.p95 != null && Number.isFinite(m.p95)
      ? [`${side === 'left' ? '左' : '右'}膝较深位置参考约 ${m.p95.toFixed(1)}°`]
      : []
  })
  if (knees.length)
    cards.push({
      id: 'motion-depth',
      label: '运动学观察',
      problem: bio?.cycles?.length ? '本次下蹲幅度参考' : '膝部活动范围参考（未确认深蹲周期）',
      reason:
        knees.join('；') +
        '。采用有效帧的95%分位数，减少单帧尖峰的影响；站直约为0°。这是全视频参考，不是最低点的精确测量或安全界线。',
      advice:
        '在脚掌稳定、躯干可控且舒适的范围练习；需要增加幅度时逐步尝试，避免为了追求深度而失去控制。',
    })
  const diff = valid('bilateral_knee_difference_deg')
  if (diff?.median != null && Number.isFinite(diff.median))
    cards.push({
      id: 'motion-symmetry',
      label: '运动学观察',
      problem: '左右腿协同参考',
      reason: `同帧双膝屈曲角差中位数约 ${diff.median.toFixed(1)}°。视角和骨架定位会影响差值，不能据此判断力量差、膝内移或肌肉代偿。`,
      advice:
        '观察双腿是否协调下蹲和起身，双脚保持稳定支撑；若持续偏向一侧，先减小幅度，并用同一正面机位复查。',
    })
  const cycles = (bio?.cycles ?? []).filter(
    (c) =>
      c.boundary_status === 'candidate_complete' &&
      typeof c.descent_to_bottom_sec === 'number' &&
      Number.isFinite(c.descent_to_bottom_sec) &&
      c.descent_to_bottom_sec > 0 &&
      typeof c.active_ascent_sec === 'number' &&
      Number.isFinite(c.active_ascent_sec) &&
      c.active_ascent_sec > 0,
  )
  if (cycles.length)
    cards.push({
      id: 'motion-tempo',
      label: '运动学观察',
      problem: '本次下蹲与起身节奏参考',
      reason:
        cycles
          .map(
            (c) =>
              `第${c.cycle_index}次下蹲约${c.descent_to_bottom_sec!.toFixed(2)}秒、起身约${c.active_ascent_sec!.toFixed(2)}秒`,
          )
          .join('；') + '。仅列完整候选动作，阶段边界为模型估计，时长本身不代表错误。',
      advice: '保持平稳、可重复的节奏，最低点继续保持控制，起身时让髋部与胸口协调上升。',
    })
  return cards
}
export function buildSquatFeedback(task: SquatTask): FeedbackCard[] {
  const observations = task.algorithm?.guidance?.observations ?? []
  const metrics = task.algorithm?.results?.biomechanics?.metrics ?? {}
  const cards: FeedbackCard[] = []
  let incompleteEvidence = false
  for (const item of observations) {
    const events = [...(item.events ?? []), ...(item.review_events ?? [])].filter(
      (e) => Number.isFinite(e.start_sec) && Number.isFinite(e.end_sec) && e.end_sec >= e.start_sec,
    )
    // A status alone is not sufficient evidence for an actionable finding.
    if (!['candidate', 'review_required'].includes(item.status)) continue
    const rule = guidance[item.type]
    if (!events.length || !rule) {
      incompleteEvidence = true
      continue
    }
    const times = [
      ...new Set(events.map((e) => `${e.start_sec.toFixed(2)}–${e.end_sec.toFixed(2)} 秒`)),
    ].join('、')
    const measurements = (rule.metricKeys ?? []).flatMap((key) => {
      const metric = metrics[key]
      return metric?.status === 'estimated' &&
        metric.median != null &&
        Number.isFinite(metric.median)
        ? [
            `补充参考：${metric.description}的全视频中位数约 ${metric.median.toFixed(1)}°，不能替代对应片段的复查。`,
          ]
        : []
    })
    cards.push({
      id: item.type,
      problem: observationNames[item.type] ?? item.type,
      label: ['plie', 'hold'].includes(item.type) ? '动作特征' : '待确认的表现',
      reason: [`算法标记片段：${times}。`, item.reason ?? '', ...measurements, rule.mechanics]
        .filter(Boolean)
        .join(' '),
      advice: rule.advice,
    })
  }
  const motionCards = kinematicFeedback(task)
  for (const card of cards) {
    if (card.id === 'torso_coordination') {
      const tempo = motionCards.find((c) => c.id === 'motion-tempo')
      if (tempo) card.reason += ' 补充运动学参考：' + tempo.reason
    }
  }
  cards.push(...motionCards)
  const unknown = observations.filter(
    (item) => item.status === 'unknown' && item.reason_code !== 'no_candidate',
  )
  if (!cards.length && (unknown.length || !observations.length || incompleteEvidence)) {
    cards.push({
      id: 'capture',
      label: '拍摄与观察',
      problem: '部分动作表现暂时无法判断',
      reason: unknown.length
        ? `本次有 ${unknown.length} 项观察证据不足。拍摄视角、遮挡或骨架所能提供的信息限制了判断；涉及脊柱曲度和足缘接触的项目仍需补充画面复核。具体原因可在算法分析结果中查看。`
        : '当前没有可用的逐项观察证据。',
      advice:
        '固定手机，让头部、双脚和地面完整入画，保持光线充足。检查膝盖内移优先拍正面；观察躯干和下蹲幅度可补充侧面。调整后重新录制。',
    })
  }
  if (!cards.length)
    cards.push({
      id: 'no-candidate',
      label: '本次观察',
      problem: '本次可分析片段未触发问题候选',
      reason: '现有规则未标记需要调整的片段，这不代表所有动作表现均已通过检查。',
      advice:
        '继续采用舒适、可控的深度，保持脚掌稳定并平稳起身；按同一机位录制后续练习，便于比较。',
    })
  return cards
}
