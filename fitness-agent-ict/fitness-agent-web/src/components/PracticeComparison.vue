<script setup lang="ts">
import { computed } from 'vue'
import { comparePracticeRecords, comparisonBlockReason } from '../services/practiceComparison'
import { issueSeverityLabels } from '../data/analysisResultOptions'
import { practicePresetLabel } from '../utils/practiceLabels'
import type { ComparedIssueSide, PracticeRecord } from '../types/practice'

const props = defineProps<{ before: PracticeRecord | null; after: PracticeRecord | null }>()
const reason = computed(() =>
  props.before && props.after
    ? comparisonBlockReason(props.before, props.after)
    : '选择两条记录后查看样例差异。',
)
const rows = computed(() =>
  props.before && props.after && !reason.value
    ? comparePracticeRecords(props.before, props.after)
    : [],
)
const labels = {
  both: '两次样例均有记录',
  before_only: '仅前一次样例有记录',
  after_only: '仅后一次样例有记录',
  uncertain: '含低置信度，暂不比较',
}

function sideLabel(side: ComparedIssueSide) {
  if (!side.count) return '无此代码记录'
  if (side.count > side.reliableCount) return `${side.count} 条 · 待复核`
  return `${side.count} 条 · 示例程度${side.severity ? issueSeverityLabels[side.severity] : '未知'}`
}
</script>

<template>
  <div class="practice-comparison" :data-status="reason ? 'blocked' : 'ready'">
    <p class="comparison-source-note">
      这里只比较固定样例的结构化字段，不评价训练进步。空记录不代表问题解决；低置信度不参与程度变化判断。
    </p>
    <p v-if="reason" class="comparison-block-reason" role="status">{{ reason }}</p>
    <template v-else-if="before && after">
      <div class="comparison-overview">
        <div>
          <span>前一次样例</span><strong>{{ before.result.issues.length }} 条记录</strong>
          <p>{{ practicePresetLabel(before) }}</p>
        </div>
        <div>
          <span>后一次样例</span><strong>{{ after.result.issues.length }} 条记录</strong>
          <p>{{ practicePresetLabel(after) }}</p>
        </div>
      </div>
      <p class="comparison-time-note">
        两次视频时长可能不同，不比较绝对时间点；文件相同或不同都不改变模拟数据的性质。
      </p>
      <p v-if="rows.length === 0" class="comparison-block-reason" role="status">
        两次样例都没有问题代码，暂无字段差异可列出；这不表示两次动作合格。
      </p>
      <div v-else class="comparison-issue-list">
        <article
          v-for="row in rows"
          :key="row.code"
          class="comparison-issue-row"
          :data-code="row.code"
          :data-status="row.status"
        >
          <div>
            <h4>{{ row.title }}</h4>
            <span class="comparison-code">{{ row.code }}</span>
          </div>
          <dl>
            <div>
              <dt>前一次</dt>
              <dd>{{ sideLabel(row.before) }}</dd>
            </div>
            <div>
              <dt>后一次</dt>
              <dd>{{ sideLabel(row.after) }}</dd>
            </div>
          </dl>
          <p class="comparison-row-status">{{ labels[row.status] }}</p>
        </article>
      </div>
    </template>
  </div>
</template>
