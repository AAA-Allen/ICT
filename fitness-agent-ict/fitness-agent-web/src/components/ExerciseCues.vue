<script setup lang="ts">
import type { Exercise } from '../types/exercise'
defineProps<{ exercise: Exercise }>()
const squatSteps = [
  {
    title: '站得舒服，脚掌稳定',
    text: '普通站距可从约髋宽至肩宽附近开始，舒适时可略宽。宽距、窄距按训练目标逐步调整，不强求统一肩宽倍数。脚尖自然朝前或稍向外，膝盖大致跟随脚尖方向，不用力向外顶膝。',
  },
  {
    title: '腰腹保持张力，腰背自然',
    text: '腰腹适度收紧，让躯干稳定，腰背保持自然位置；不要完全放松腰部，也不刻意过度挺腰。躯干可以随下蹲自然前倾，不能仅凭腰背有发力感就认定肌肉代偿。',
  },
  {
    title: '屈髋屈膝，缓慢下蹲',
    text: '髋部向后、向下移动，髋膝协调弯曲。双脚保持稳定，避免膝盖突然内塌、身体明显晃动。出现持续抖动或难以控制时，先减小深度或休息。',
  },
  {
    title: '最低点保持控制',
    text: '蹲到自己能稳定控制、没有疼痛的深度。最低点继续保持躯干与腿部张力，不突然松垮或借弹震起身；是否停顿取决于训练目标，不强求蹲到同一深度。',
  },
  {
    title: '平稳推地，协调起身',
    text: '用双脚平稳推地，髋部与胸口协调上升，避免臀部先猛抬、躯干明显滞后或左右晃动。让前脚掌与脚跟保持稳定支撑，避免向前扑或向后倒。',
  },
  {
    title: '按能控制的节奏完成',
    text: '初学时可尝试下蹲约 2–3 秒、起身约 1–2 秒，吸气下蹲、呼气起身。这是节奏示例，不是达标秒数；先以平稳、能控制为准。',
  },
]
</script>

<template>
  <section class="exercise-cues" aria-labelledby="exercise-cues-title">
    <header>
      <p class="cues-eyebrow">BEFORE YOU MOVE</p>
      <h3 id="exercise-cues-title">{{ exercise.name }} · 动作要领</h3>
      <p>先熟悉动作，再开始这一次练习。</p>
    </header>
    <template v-if="exercise.id === 'bodyweight_squat'">
      <p class="muscle-focus">
        <strong>主要练习部位</strong>股四头肌、臀肌；躯干肌群协助稳定身体。
      </p>
      <ol class="cues-list">
        <li v-for="step in squatSteps" :key="step.title">
          <h4>{{ step.title }}</h4>
          <p>{{ step.text }}</p>
        </li>
      </ol>
      <aside class="cues-reminder">
        <strong>先记住这三点</strong>
        <p>脚掌稳 · 腰腹有张力 · 髋与胸口一起起身</p>
      </aside>
      <p class="heel-cue">
        <strong>脚跟总是不由自主地抬起？</strong
        >先减小下蹲深度、调整站距；如果仍难保持脚跟稳定接触支撑面，可在教练指导下，将稳定、防滑、左右等高的专用深蹲楔形垫放在双脚脚跟下方，让前脚掌继续稳定接触地面，再从较浅的自重深蹲尝试。
      </p>
      <details class="heel-support">
        <summary>脚跟容易抬起或重心不稳，怎样使用垫高支撑？</summary>
        <p>
          先尝试减少下蹲深度、调整舒适站距，必要时在牢固支撑物旁练习。如果脚跟仍不由自主地抬起，建议请教练确认是否适合垫高。垫子应放在脚跟下方提供支撑，而不是仅放在脚跟后面；确保双侧高度一致、支撑牢固，前脚掌仍能稳定接触地面。
        </p>
        <p>
          不建议初学者随手叠放杠铃片、书本等可能滑动或翻倒的物品。垫高会改变动作条件，并不是所有重心不稳的通用解决方法；出现疼痛或仍站不稳时，停止本次尝试。
        </p>
      </details>
      <p class="cues-stop">
        出现尖锐、突然或不断加重的疼痛时停止练习；反复疼痛或明显不稳时，寻求专业评估。
      </p>
      <p class="cues-sources">
        参考：<a
          href="https://www.acefitness.org/resources/everyone/exercise-library/135/bodyweight-squat/"
          target="_blank"
          rel="noopener noreferrer"
          >ACE 动作指导</a
        ><a
          href="https://www.hss.edu/health-library/move-better/back-pain-after-squats"
          target="_blank"
          rel="noopener noreferrer"
          >HSS 深蹲指导</a
        >
      </p>
    </template>
    <template v-else
      ><p>{{ exercise.description }}</p>
      <p class="cues-stop">
        该动作的详细要领正在整理。请先确认动作方法与器械设置，再开始练习。
      </p></template
    >
  </section>
</template>

<style scoped>
.exercise-cues {
  margin: 24px 0;
  padding: clamp(20px, 4vw, 32px);
  border: 1px solid #d6dfcf;
  border-radius: 16px;
  background: #fcfdf9;
  color: #28493b;
  line-height: 1.85;
}
.cues-eyebrow {
  margin: 0;
  letter-spacing: 0.15em;
  font-size: 11px;
  color: #6c7b64;
}
h3 {
  margin: 8px 0;
  font-size: 22px;
}
header > p:last-child {
  color: #6b7865;
}
.muscle-focus {
  background: #edf3e8;
  padding: 14px 18px;
  border-radius: 10px;
  font-size: 14px;
}
.muscle-focus strong {
  display: block;
}
.cues-list {
  padding-left: 24px;
  display: grid;
  gap: 16px;
  margin: 24px 0;
}
.cues-list li {
  padding-left: 7px;
}
.cues-list li::marker {
  color: #688359;
  font-weight: 700;
}
h4 {
  margin: 0 0 5px;
  font-size: 16px;
}
.cues-list p {
  margin: 0;
  font-size: 14px;
  color: #52644d;
}
.cues-reminder {
  padding: 16px 20px;
  border-left: 3px solid #789468;
  background: #edf2e5;
  border-radius: 0 10px 10px 0;
}
.cues-reminder p {
  margin: 4px 0 0;
}
.heel-support {
  margin-top: 20px;
  padding: 15px 18px;
  border: 1px solid #dae2d2;
  border-radius: 10px;
  font-size: 14px;
}
.heel-cue {
  font-size: 14px;
  line-height: 1.85;
  margin-top: 18px;
}
summary {
  cursor: pointer;
  font-weight: 650;
}
.cues-stop {
  font-size: 13px;
  color: #746750;
  margin-top: 20px;
}
.cues-sources {
  font-size: 12px;
  color: #6c7b64;
}
a {
  color: #476b38;
  margin-right: 14px;
  text-underline-offset: 3px;
}
@media (min-width: 960px) {
  .cues-list {
    grid-template-columns: 1fr 1fr;
    gap: 24px 36px;
  }
}
</style>
