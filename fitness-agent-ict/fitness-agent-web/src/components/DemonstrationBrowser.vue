<script setup lang="ts">
import { computed, ref } from 'vue'
import DemonstrationPlayer from './DemonstrationPlayer.vue'
import { useDemonstrationMatch } from '../composables/useDemonstrationMatch'
import { demonstrationScenarios } from '../data/demonstrations'
import { isReliableIssue } from '../utils/issueConfidence'
import { catalogRevision, catalogSourceLabel } from '../services/catalogSource'
import type { MockAnalysisResult } from '../types/analysisResult'
import type { DemonstrationQuery, DemonstrationScenario } from '../types/demonstration'

const props = defineProps<{ result: MockAnalysisResult }>()
const selectedIssueId = ref(props.result.issues[0]?.id ?? '')
const scenario = ref<DemonstrationScenario>('normal')
const issue = computed(() => props.result.issues.find((item) => item.id === selectedIssueId.value))
const query = computed<DemonstrationQuery | null>(() => {
  if (!issue.value || !isReliableIssue(issue.value)) return null
  return {
    catalogRevision: catalogRevision.value,
    taskId: props.result.taskId,
    exerciseId: props.result.exerciseId,
    exerciseName: props.result.exerciseName,
    issue: issue.value,
    scenario: scenario.value,
  }
})
const { state, reload } = useDemonstrationMatch(() => query.value)
</script>

<template>
  <section
    class="demonstration-browser"
    aria-labelledby="demonstration-title"
    :data-status="state.status"
  >
    <div class="results-heading">
      <div>
        <p class="eyebrow section-eyebrow">EXPLORE THE MATERIALS</p>
        <h3 id="demonstration-title">示范资料匹配</h3>
      </div>
      <span class="results-source-badge">{{ catalogSourceLabel }} · 规则匹配</span>
    </div>
    <p class="demonstration-source-note">
      按当前动作、固定问题代码和示例程度查询当前资料快照，不是 Agent
      推荐。正式教学素材待补充；可播放的片段仅用于验证播放器。
    </p>

    <p v-if="result.issues.length === 0" class="demonstration-state" role="status">
      当前样例没有问题记录，不自动匹配纠错资料。这不代表视频动作合格。
    </p>
    <template v-else>
      <div class="demonstration-issue-choices" role="group" aria-label="选择要查询资料的模拟问题">
        <button
          v-for="(item, index) in result.issues"
          :key="item.id"
          type="button"
          :aria-pressed="selectedIssueId === item.id"
          :data-issue-id="item.id"
          @click="selectedIssueId = item.id"
        >
          {{ index + 1 }}. {{ item.title }}
        </button>
      </div>
      <p v-if="!query" class="demonstration-state demonstration-uncertain" role="status">
        当前记录置信度不足，暂不匹配纠错资料。可以使用上方“定位该时段”回看复核。
      </p>
      <template v-else>
        <div class="demonstration-controls">
          <label for="demonstration-scenario">资料查询场景</label>
          <select id="demonstration-scenario" v-model="scenario">
            <option
              v-for="option in demonstrationScenarios"
              :key="option.value"
              :value="option.value"
            >
              {{ option.label }}
            </option>
          </select>
          <span>切换后立即查询，不改变上方分析结果。</span>
        </div>
        <p v-if="state.status === 'loading'" class="demonstration-state" role="status">
          正在查询本地资料…
        </p>
        <div v-else-if="state.status === 'failed'" class="demonstration-state" role="status">
          <h4>资料查询失败（模拟）</h4>
          <p>可以重试；恢复“正常查询”后可查看本地资料。练习视频和模拟结果仍然保留。</p>
          <button type="button" class="demonstration-retry-query" @click="reload">重试查询</button>
        </div>
        <div v-else-if="state.status === 'no_match'" class="demonstration-state" role="status">
          <h4>暂无匹配资料</h4>
          <p>
            当前动作、问题代码和程度未匹配到条目。可以切换其他问题查看，不会替换成无关动作的视频。
          </p>
        </div>
        <article
          v-else-if="state.status === 'matched'"
          class="demonstration-match"
          :data-record-id="state.match.record.id"
        >
          <div class="demonstration-match-copy">
            <p class="demonstration-match-label">本地规则匹配条目</p>
            <h4>{{ state.match.record.title }}</h4>
            <p>{{ state.match.record.description }}</p>
            <h5>匹配依据（固定样例）</h5>
            <ul class="demonstration-reasons">
              <li v-for="reason in state.match.reasons" :key="reason">{{ reason }}</li>
            </ul>
            <p class="demonstration-material-pending">正式教学素材：待补充</p>
          </div>
          <div class="demonstration-match-media">
            <template v-if="state.match.record.media">
              <p class="demonstration-sample-label">播放器测试片段 · 非动作教学</p>
              <DemonstrationPlayer
                :key="`${result.taskId}-${issue!.id}-${scenario}-${state.match.record.id}`"
                :src="state.match.record.media.src"
                :simulate-failure="scenario === 'playback_failure'"
              />
              <p id="demonstration-media-description" class="demonstration-media-description">
                {{ state.match.record.media.description }}
              </p>
            </template>
            <div v-else class="demonstration-missing-material" role="status">
              <span aria-hidden="true">▷</span>
              <h5>条目已匹配，视频素材待补充</h5>
              <p>没有可播放文件。当前不加载播放器，也不使用上方练习视频充当示范。</p>
            </div>
          </div>
        </article>
      </template>
    </template>
  </section>
</template>
