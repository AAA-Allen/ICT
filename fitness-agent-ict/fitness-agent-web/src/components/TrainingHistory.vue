<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import PracticeComparison from './PracticeComparison.vue'
import { issueSeverityLabels } from '../data/analysisResultOptions'
import { MAX_PRACTICE_RECORDS } from '../services/practiceHistoryStorage'
import {
  configurationLabels,
  formatPracticeDate,
  practiceOptionLabel,
  practicePresetLabel,
} from '../utils/practiceLabels'
import { formatVideoTime } from '../utils/videoTime'
import { isReliableIssue } from '../utils/issueConfidence'
import type { PracticeComparisonPair, PracticeRecord } from '../types/practice'

const props = defineProps<{
  records: PracticeRecord[]
  storageWarning: string
  message: string
  busy: boolean
  comparisonPair: PracticeComparisonPair | null
}>()
const emit = defineEmits<{
  again: [recordId: string]
  remove: [recordId: string]
  clear: []
  persist: []
  review: []
}>()
const exerciseFilter = ref('')
const selectedId = ref(props.records[0]?.id ?? '')
const beforeId = ref('')
const afterId = ref('')
const confirmingClear = ref(false)
const exerciseOptions = computed(() => [
  ...new Map(
    props.records.map((record) => [record.result.exerciseId, record.result.exerciseName]),
  ).entries(),
])
const filteredRecords = computed(() =>
  props.records.filter(
    (record) => !exerciseFilter.value || record.result.exerciseId === exerciseFilter.value,
  ),
)
const selected = computed(
  () => props.records.find((record) => record.id === selectedId.value) ?? null,
)
const before = computed(() => props.records.find((record) => record.id === beforeId.value) ?? null)
const after = computed(() => props.records.find((record) => record.id === afterId.value) ?? null)

watch(
  filteredRecords,
  (records) => {
    if (!records.some((record) => record.id === selectedId.value))
      selectedId.value = records[0]?.id ?? ''
  },
  { immediate: true },
)
watch(
  () => props.records,
  (records) => {
    if (!records.some((record) => record.result.exerciseId === exerciseFilter.value))
      exerciseFilter.value = ''
    if (!records.some((record) => record.id === beforeId.value)) beforeId.value = ''
    if (!records.some((record) => record.id === afterId.value)) afterId.value = ''
    if (records.length === 0) confirmingClear.value = false
  },
)
watch(
  () => props.comparisonPair,
  (pair) => {
    if (!pair) return
    beforeId.value = pair.beforeId
    afterId.value = pair.afterId
    selectedId.value = pair.afterId
    exerciseFilter.value = ''
  },
)

function selectRecord(id: string) {
  selectedId.value = id
  emit('review')
}
function chooseBefore(id: string) {
  beforeId.value = id
  emit('review')
}
function chooseAfter(id: string) {
  afterId.value = id
  emit('review')
}
function clearRecords() {
  confirmingClear.value = false
  emit('clear')
}
</script>

<template>
  <section class="training-history" aria-labelledby="training-history-title">
    <div class="section-heading">
      <div>
        <p class="eyebrow section-eyebrow">RETURN AND REVIEW</p>
        <h2 id="training-history-title" tabindex="-1">本地练习记录与样例比较</h2>
      </div>
      <span class="local-only-tag"
        >{{ records.length }} / {{ MAX_PRACTICE_RECORDS }} 条 · 本机</span
      >
    </div>
    <p class="history-source-note">
      每次成功模拟自动记录摘要；重新模拟同一文件也会新增一条。记录包含文件名、训练条件和固定结果，不保存视频文件或播放地址，也不代表真实训练效果。
    </p>
    <p v-if="message" class="history-message" role="status">{{ message }}</p>
    <div v-if="storageWarning" class="history-storage-warning" role="status">
      <p>{{ storageWarning }}</p>
      <button type="button" class="history-retry-save" @click="emit('persist')">重试保存</button>
    </div>
    <p v-if="records.length === 0" class="history-empty" role="status">
      还没有本地记录。完成一次“正常完成”的模拟后，结果摘要会显示在这里。失败、超时、取消和质量不足不会新增记录。
    </p>
    <template v-else>
      <div class="history-toolbar">
        <label for="history-exercise-filter">按动作查看</label>
        <select id="history-exercise-filter" v-model="exerciseFilter">
          <option value="">全部动作</option>
          <option v-for="[id, name] in exerciseOptions" :key="id" :value="id">{{ name }}</option>
        </select>
        <button
          type="button"
          class="history-clear-button"
          @click="confirmingClear = !confirmingClear"
        >
          清空本地记录
        </button>
      </div>
      <div
        v-if="confirmingClear"
        class="history-clear-confirm"
        role="group"
        aria-label="确认清空本地摘要"
      >
        <p>清空这 {{ records.length }} 条本地摘要？当前视频和分析结果会保留。</p>
        <button type="button" class="history-confirm-clear" @click="clearRecords">确认清空</button
        ><button type="button" @click="confirmingClear = false">取消</button>
      </div>
      <div class="history-workspace">
        <div class="history-record-list" role="group" aria-label="已保存的模拟记录">
          <button
            v-for="record in filteredRecords"
            :key="record.id"
            type="button"
            class="history-record-select"
            :aria-pressed="selectedId === record.id"
            :data-record-id="record.id"
            @click="selectRecord(record.id)"
          >
            <span class="history-record-heading"
              ><strong>{{ record.result.exerciseName }}</strong
              ><span>{{ record.result.issues.length }} 条样例</span></span
            >
            <span
              >{{ formatPracticeDate(record.createdAt) }} · {{ practicePresetLabel(record) }}</span
            >
            <span class="history-file-name">{{ record.result.videoName }}</span>
          </button>
        </div>
        <article v-if="selected" class="history-detail" :data-record-id="selected.id">
          <p class="history-detail-label">
            已保存摘要 · {{ formatPracticeDate(selected.createdAt) }}
          </p>
          <h3>{{ selected.result.exerciseName }} · {{ practicePresetLabel(selected) }}</h3>
          <p class="history-detail-file">{{ selected.result.videoName }}</p>
          <div class="history-config-labels">
            <span v-for="label in configurationLabels(selected.configuration)" :key="label">{{
              label
            }}</span>
          </div>
          <p class="history-record-id">编号：{{ selected.id }}</p>
          <p v-if="selected.previousRecordId" class="history-linked-note">
            来自一次“再次练习”流程；前次摘要{{
              records.some((item) => item.id === selected!.previousRecordId)
                ? '仍在本地列表中'
                : '已不在本地列表中'
            }}。
          </p>
          <ul v-if="selected.result.issues.length" class="history-saved-issues">
            <li v-for="issue in selected.result.issues" :key="issue.id">
              <strong>{{ issue.title }}</strong
              ><span
                >示例程度{{ issueSeverityLabels[issue.severity] }} · 置信度
                {{ Math.round(issue.confidence * 100) }}%{{
                  isReliableIssue(issue) ? '' : ' · 待复核'
                }}</span
              >
            </li>
          </ul>
          <p v-else class="history-empty-result">该固定样例没有问题记录，不表示视频动作合格。</p>
          <p class="history-no-video">
            视频时长：{{
              formatVideoTime(
                selected.result.durationSeconds === null
                  ? null
                  : selected.result.durationSeconds * 1000,
              )
            }}。历史只保存摘要，无法恢复或定位过去的视频。
          </p>
          <div class="history-detail-actions">
            <button
              type="button"
              class="practice-again-button"
              :disabled="busy"
              @click="emit('again', selected.id)"
            >
              沿用条件，再次练习 ↗</button
            ><button type="button" class="history-set-before" @click="chooseBefore(selected.id)">
              设为前一次</button
            ><button type="button" class="history-set-after" @click="chooseAfter(selected.id)">
              设为后一次</button
            ><button
              type="button"
              class="history-delete-button"
              @click="emit('remove', selected.id)"
            >
              删除此摘要
            </button>
          </div>
          <p v-if="busy" class="history-busy-note">当前模拟进行中，完成或取消后可以再次练习。</p>
        </article>
      </div>
      <div class="history-comparison-section">
        <h3>比较两次固定样例</h3>
        <div class="history-comparison-controls">
          <div>
            <label for="comparison-before">前一次记录</label
            ><select id="comparison-before" v-model="beforeId" @change="emit('review')">
              <option value="">请选择</option>
              <option v-for="record in records" :key="record.id" :value="record.id">
                {{ practiceOptionLabel(record) }}
              </option>
            </select>
          </div>
          <button
            type="button"
            class="comparison-swap-button"
            aria-label="交换前后记录"
            @click="
              ;[beforeId, afterId] = [afterId, beforeId]
              emit('review')
            "
          >
            ⇄
          </button>
          <div>
            <label for="comparison-after">后一次记录</label
            ><select id="comparison-after" v-model="afterId" @change="emit('review')">
              <option value="">请选择</option>
              <option v-for="record in records" :key="record.id" :value="record.id">
                {{ practiceOptionLabel(record) }}
              </option>
            </select>
          </div>
        </div>
        <PracticeComparison :before="before" :after="after" />
      </div>
    </template>
    <p class="history-storage-note">
      最多保留最近
      {{ MAX_PRACTICE_RECORDS }}
      条。仅在同一浏览器、同一网站地址下保存；更换端口或清除浏览器数据会影响可见记录。存储不可用时可继续使用本页。
    </p>
  </section>
</template>
