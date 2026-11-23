<script setup lang="ts">
import { computed } from 'vue'
import type { SquatTask } from '../services/squatBackend'
import { buildSquatFeedback, guidanceSources, observationNames } from '../services/squatFeedback'

const props = defineProps<{ task: SquatTask }>()
const emit = defineEmits<{ speak: [text: string] }>()
const cards = computed(() => buildSquatFeedback(props.task))
const motionCount = computed(() => cards.value.filter((c) => c.id.startsWith('motion-')).length)
const unresolvedCount = computed(
  () =>
    (props.task.algorithm?.guidance?.observations ?? []).filter(
      (o) => o.status === 'unknown' && o.reason_code !== 'no_candidate',
    ).length,
)
const ruleCount = computed(
  () =>
    (props.task.algorithm?.guidance?.observations ?? []).filter(
      (o) =>
        ['candidate', 'review_required'].includes(o.status) &&
        (o.events?.length ?? 0) + (o.review_events?.length ?? 0) > 0,
    ).length,
)
const statusName = (status: string, code?: string) =>
  status === 'candidate'
    ? '待复核候选'
    : status === 'review_required'
      ? '需查看视频确认'
      : code === 'no_candidate'
        ? '未触发候选'
        : '无法判断'
</script>

<template>
  <section class="squat-feedback" aria-labelledby="squat-feedback-title">
    <header class="feedback-heading">
      <div>
        <p class="eyebrow">YOUR NEXT STEP</p>
        <h4 id="squat-feedback-title">本次练习反馈</h4>
      </div>
      <span class="feedback-badge">动作观察与训练建议</span>
    </header>
    <p class="feedback-intro">
      先看值得关注的表现与下一次怎么调整；疑似表现请结合对应视频片段确认。
    </p>
    <p class="feedback-intro">
      骨架规则：{{ ruleCount }} 项候选 · 生物力学运动学：{{ motionCount }}
      项可用观察。两条路径共用模型估计骨架，综合展示可用证据。
    </p>
    <article v-for="(card, index) in cards" :key="card.id" class="feedback-card">
      <div class="card-heading">
        <span class="card-number">{{ String(index + 1).padStart(2, '0') }}</span
        ><span>{{ card.label }}</span>
      </div>
      <dl>
        <div>
          <dt>问题</dt>
          <dd class="problem-title">{{ card.problem }}</dd>
        </div>
        <div>
          <dt>理由</dt>
          <dd>{{ card.reason }}</dd>
        </div>
        <div class="advice-row">
          <dt>建议</dt>
          <dd>{{ card.advice }}</dd>
        </div>
      </dl>
      <button class="advice-speech" @click="emit('speak', card.advice)">播报建议</button>
    </article>
    <p class="guidance-sources">
      指导参考：<template v-for="source in guidanceSources" :key="source.url"
        ><a :href="source.url" target="_blank" rel="noopener noreferrer">{{
          source.title
        }}</a></template
      >
    </p>
    <p v-if="unresolvedCount && motionCount" class="feedback-intro">
      另有
      {{
        unresolvedCount
      }}
      项表现尚未判断，原因可在下方展开查看；以上运动学观察不代表这些项目已通过检查。
    </p>
    <details :key="task.task_id" class="algorithm-details">
      <summary>
        <span>算法分析结果</span><span class="expand-hint">点击下拉展开</span
        ><span class="collapse-hint">点击收起</span>
      </summary>
      <div class="algorithm-content">
        <h5>动作与画面质量</h5>
        <p>
          动作次数：{{ task.algorithm?.results?.features?.cycle_count ?? '未知' }} · 有效帧比例：{{
            task.algorithm?.results?.quality?.valid_knee_fraction == null
              ? '未知'
              : Math.round(task.algorithm.results.quality.valid_knee_fraction * 100) + '%'
          }}
        </p>
        <p>以下为模型估计和候选证据；未触发或无法判断均不能作为动作全部正确的证明。</p>
        <h5>逐项观察</h5>
        <ul>
          <li v-for="item in task.algorithm?.guidance?.observations" :key="item.type">
            <strong>{{ observationNames[item.type] ?? item.type }}</strong
            >：{{ statusName(item.status, item.reason_code) }}
            <p>{{ item.reason }}</p>
            <p
              v-for="(event, index) in [...(item.events ?? []), ...(item.review_events ?? [])]"
              :key="index"
            >
              {{ event.start_sec }}–{{ event.end_sec }} 秒<span v-if="event.phase">
                · 阶段：{{ event.phase }}</span
              >
            </p>
          </li>
        </ul>
        <h5>生物力学指标（模型估计，全视频中位数）</h5>
        <ul>
          <li v-for="(metric, key) in task.algorithm?.results?.biomechanics?.metrics" :key="key">
            {{ metric.description }}：{{
              metric.status === 'estimated' && metric.median != null
                ? metric.median.toFixed(2) + (metric.unit === 'deg' ? '°' : '（相对腿长）')
                : '未知（当前视角或参考帧不足）'
            }}
          </li>
        </ul>
        <p v-if="!task.algorithm?.results?.biomechanics?.metrics">本次未返回生物力学指标。</p>
        <section v-if="task.agent_status === 'COMPLETED' && task.agent">
          <h5>Agent 补充反馈</h5>
          <p>{{ task.agent.summary }}</p>
          <ul>
            <li v-for="(suggestion, index) in task.agent.suggestions" :key="index">
              {{ suggestion.text }}
            </li>
          </ul>
        </section>
      </div>
    </details>
  </section>
</template>

<style scoped>
.squat-feedback {
  margin-top: 28px;
  color: #25443c;
}
.feedback-heading,
.card-heading {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.feedback-heading {
  justify-content: space-between;
}
.eyebrow {
  font-size: 11px;
  letter-spacing: 0.16em;
  color: #64786b;
}
.feedback-heading h4 {
  font-size: 24px;
  margin: 6px 0;
}
.feedback-badge {
  font-size: 12px;
  padding: 7px 12px;
  border-radius: 20px;
  background: #e5eddf;
}
.feedback-intro,
.guidance-sources {
  color: #607066;
  font-size: 14px;
  line-height: 1.8;
}
.feedback-card {
  padding: 24px;
  margin: 18px 0;
  border: 1px solid #d5dfd1;
  border-radius: 16px;
  background: #fcfdf9;
}
.card-heading {
  font-size: 12px;
  color: #607066;
}
.card-number {
  font-weight: 700;
  color: #315c4d;
}
dl {
  margin: 16px 0;
}
dl > div {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr);
  gap: 12px;
  margin: 14px 0;
}
dt {
  font-size: 13px;
  font-weight: 700;
  padding-top: 3px;
}
dd {
  margin: 0;
  line-height: 1.9;
  font-size: 15px;
}
.problem-title {
  font-size: 19px;
  font-weight: 650;
}
.advice-row {
  padding: 16px;
  border-radius: 10px;
  background: #edf3e8;
}
.advice-speech {
  border: 1px solid #c3d2bd;
  background: transparent;
  border-radius: 8px;
  padding: 8px 13px;
  color: #315343;
  cursor: pointer;
}
.guidance-sources a {
  display: inline-block;
  color: #42684e;
  margin-right: 16px;
  text-underline-offset: 3px;
}
.algorithm-details {
  margin-top: 24px;
  border: 1px solid #d1dccb;
  border-radius: 12px;
  overflow: hidden;
}
summary {
  cursor: pointer;
  padding: 18px 20px;
  font-weight: 650;
  background: #edf1e7;
}
.expand-hint {
  margin-left: 12px;
  font-size: 12px;
  font-weight: 400;
  color: #61715d;
}
.algorithm-content {
  padding: 20px;
  font-size: 14px;
  line-height: 1.8;
}
.algorithm-content h5 {
  font-size: 15px;
  margin: 18px 0 10px;
}
.algorithm-content li {
  margin-bottom: 12px;
}
.algorithm-content p {
  margin: 6px 0;
}
.collapse-hint {
  display: none;
  margin-left: 12px;
  font-size: 12px;
  font-weight: 400;
  color: #61715d;
}
details[open] .expand-hint {
  display: none;
}
details[open] .collapse-hint {
  display: inline;
}
@media (max-width: 600px) {
  .feedback-card {
    padding: 16px;
  }
  dl > div {
    grid-template-columns: 1fr;
    gap: 4px;
  }
  .advice-row {
    padding: 12px;
  }
  .feedback-badge {
    display: none;
  }
}
</style>
