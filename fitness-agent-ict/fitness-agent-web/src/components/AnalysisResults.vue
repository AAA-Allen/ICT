<script setup lang="ts">
import { computed } from 'vue'
import DemonstrationBrowser from './DemonstrationBrowser.vue'
import { issueSeverityLabels, resultPresets } from '../data/analysisResultOptions'
import { canLocateIssue, formatVideoTime } from '../utils/videoTime'
import { isReliableIssue as isReliable } from '../utils/issueConfidence'
import type { AnalysisIssue, MockAnalysisResult } from '../types/analysisResult'

const props = defineProps<{ result: MockAnalysisResult; activeIssueId: string | null }>()
const emit = defineEmits<{ locate: [issueId: string] }>()
const presetName = computed(
  () => resultPresets.find((option) => option.value === props.result.preset)?.label,
)
const timedIssues = computed(() =>
  props.result.issues.filter((issue) => canLocateIssue(issue, props.result.durationSeconds)),
)

function confidenceLabel(confidence: number) {
  return Number.isFinite(confidence) && confidence >= 0 && confidence <= 1
    ? `${Math.round(confidence * 100)}%`
    : '未知'
}

function issueRange(issue: AnalysisIssue) {
  return canLocateIssue(issue, props.result.durationSeconds)
    ? `${formatVideoTime(issue.startMs)} – ${formatVideoTime(issue.endMs)}`
    : '时段暂不可用'
}

function markerStyle(issue: AnalysisIssue) {
  if (!canLocateIssue(issue, props.result.durationSeconds)) return {}
  const totalMs = props.result.durationSeconds! * 1000
  return {
    left: `${(issue.startMs / totalMs) * 100}%`,
    width: `${((issue.endMs - issue.startMs) / totalMs) * 100}%`,
  }
}
</script>

<template>
  <section
    class="analysis-results"
    aria-labelledby="analysis-results-title"
    :data-task-id="result.taskId"
    :data-preset="result.preset"
  >
    <div class="results-heading">
      <div>
        <p class="eyebrow section-eyebrow">REVIEW THE EXAMPLE</p>
        <h3 id="analysis-results-title">结构化模拟结果</h3>
      </div>
      <span class="results-source-badge">本地固定样例</span>
    </div>
    <p class="results-source-note">
      这些问题、置信度和反馈来自固定样例。时段按视频时长缩放，不代表真实检测位置，也不评价当前动作。
    </p>
    <p class="result-input-info">{{ result.exerciseName }} · {{ result.videoName }}</p>

    <dl class="results-overview">
      <div>
        <dt>结果样例</dt>
        <dd>{{ presetName }}</dd>
      </div>
      <div>
        <dt>问题记录</dt>
        <dd class="result-issue-count">{{ result.issues.length }} 条</dd>
      </div>
      <div>
        <dt>视频总时长</dt>
        <dd>
          {{
            formatVideoTime(result.durationSeconds === null ? null : result.durationSeconds * 1000)
          }}
        </dd>
      </div>
    </dl>

    <div v-if="result.issues.length === 0" class="results-empty" role="status">
      <h4>样例中没有问题记录</h4>
      <p>这里只表示当前模拟样例为空，不代表你的视频动作合格。可以改选“问题示例”再运行。</p>
    </div>

    <template v-else>
      <div
        v-if="timedIssues.length > 0"
        class="issue-timeline"
        role="group"
        aria-label="模拟问题时间线"
      >
        <div class="timeline-heading">
          <h4>示例时段概览</h4>
          <span>点击某一行定位，视频会暂停</span>
        </div>
        <div class="timeline-axis" aria-hidden="true">
          <span>0:00.00</span><span>{{ formatVideoTime(result.durationSeconds! * 1000) }}</span>
        </div>
        <div v-for="(issue, index) in timedIssues" :key="issue.id" class="timeline-item">
          <div class="timeline-label">
            <span>{{ index + 1 }}. {{ issue.title }}</span
            ><span>{{ issueRange(issue) }}</span>
          </div>
          <button
            type="button"
            class="issue-timeline-button"
            :class="{ 'is-selected': activeIssueId === issue.id }"
            :aria-label="`定位示例时段：${issue.title}，${issueRange(issue)}`"
            :aria-pressed="activeIssueId === issue.id"
            :data-issue-id="issue.id"
            @click="emit('locate', issue.id)"
          >
            <span class="timeline-marker" :style="markerStyle(issue)" aria-hidden="true">{{
              index + 1
            }}</span>
          </button>
        </div>
      </div>
      <p v-else class="results-time-unavailable" role="status">
        视频时长暂不可读，未生成示例时段。问题文字仍可查看；播放视频确认时长后，可重新模拟。
      </p>

      <div class="analysis-issue-list">
        <article
          v-for="(issue, index) in result.issues"
          :key="issue.id"
          class="analysis-issue-card"
          :class="{ 'is-selected': activeIssueId === issue.id, 'is-uncertain': !isReliable(issue) }"
          :data-issue-id="issue.id"
          :data-start-ms="issue.startMs"
          :data-end-ms="issue.endMs"
        >
          <div class="issue-card-heading">
            <h4>{{ index + 1 }}. {{ issue.title }}<span class="issue-example-tag">示例</span></h4>
            <span class="issue-severity">示例程度：{{ issueSeverityLabels[issue.severity] }}</span>
          </div>
          <div class="issue-confidence-row">
            <span>示例置信度</span
            ><strong class="issue-confidence">{{ confidenceLabel(issue.confidence) }}</strong>
          </div>
          <meter
            class="issue-confidence-meter"
            min="0"
            max="1"
            :value="
              Number.isFinite(issue.confidence) ? Math.max(0, Math.min(1, issue.confidence)) : 0
            "
            :aria-label="`示例置信度 ${confidenceLabel(issue.confidence)}`"
          />
          <p v-if="isReliable(issue)" class="issue-feedback">{{ issue.feedback }}</p>
          <p v-else class="issue-uncertain-note">
            置信度不足，仅供回看复核，不提供纠错反馈。这是模拟数值，不是对视频的真实判断。
          </p>
          <div class="issue-location-row">
            <span class="issue-time">{{ issueRange(issue) }}</span
            ><button
              type="button"
              class="locate-issue-button"
              :disabled="!canLocateIssue(issue, result.durationSeconds)"
              :aria-pressed="activeIssueId === issue.id"
              :data-issue-id="issue.id"
              @click="emit('locate', issue.id)"
            >
              {{ activeIssueId === issue.id ? '已定位此时段' : '定位该时段' }} ↗
            </button>
          </div>
        </article>
      </div>
    </template>
    <DemonstrationBrowser :key="result.taskId" :result="result" />
  </section>
</template>
