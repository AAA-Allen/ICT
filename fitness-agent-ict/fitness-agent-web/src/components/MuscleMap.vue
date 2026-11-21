<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  bodyOutline,
  isMuscleVisible,
  muscleMapRegions,
  preferredBodyView,
} from '../data/muscleMap'
import type { MuscleGroup, MuscleId } from '../types/muscle'
import type { BodyView } from '../types/muscleMap'

const props = defineProps<{
  muscles: MuscleGroup[]
  selectedId: MuscleId | null
  view: BodyView
}>()
const emit = defineEmits<{
  select: [muscleId: MuscleId]
  'update:view': [view: BodyView]
}>()

const hoveredId = ref<MuscleId | null>(null)
const focusedId = ref<MuscleId | null>(null)
const viewName = computed(() => (props.view === 'front' ? '正面' : '背面'))
const regions = computed(() => muscleMapRegions[props.view])
const selectedIsVisible = computed(
  () => !props.selectedId || isMuscleVisible(props.view, props.selectedId),
)
const previewId = computed(() => hoveredId.value ?? focusedId.value)

function muscleName(id: MuscleId) {
  return props.muscles.find((muscle) => muscle.id === id)?.name ?? id
}

function focusRegion(id: MuscleId) {
  focusedId.value = id
  hoveredId.value = null
}

const selectionMessage = computed(() => {
  if (previewId.value && previewId.value !== props.selectedId) {
    return `${muscleName(previewId.value)} · 点击或按 Enter 选择`
  }
  if (!props.selectedId) return '点击着色部位，选择你的目标肌群'
  return selectedIsVisible.value
    ? `已选择：${muscleName(props.selectedId)}`
    : `已选择${muscleName(props.selectedId)}，当前${viewName.value}不显示该部位`
})

function showSelectedMuscle() {
  if (props.selectedId) emit('update:view', preferredBodyView(props.selectedId))
}

watch(
  () => props.view,
  () => {
    hoveredId.value = null
    focusedId.value = null
  },
)
</script>

<template>
  <div class="body-map">
    <div class="body-view-switcher" role="group" aria-label="人体视图">
      <button
        type="button"
        class="body-view-button"
        :aria-pressed="view === 'front'"
        @click="emit('update:view', 'front')"
      >
        正面
      </button>
      <button
        type="button"
        class="body-view-button"
        :aria-pressed="view === 'back'"
        @click="emit('update:view', 'back')"
      >
        背面
      </button>
    </div>

    <svg
      class="body-map-svg"
      viewBox="0 0 320 540"
      role="group"
      aria-labelledby="body-map-title"
      aria-describedby="body-map-description"
      :data-view="view"
    >
      <title id="body-map-title">{{ viewName }}肌群选择示意图</title>
      <desc id="body-map-description">
        着色部位可以选择，左右两侧属于同一肌群。可以用 Tab 聚焦，再按 Enter
        或空格选择。灰色部位不参与选择。
      </desc>
      <g class="body-map-decoration" aria-hidden="true">
        <path d="M160 12 V524" class="body-center-guide" />
        <ellipse cx="160" cy="520" rx="59" ry="6" class="body-ground" />
        <path :d="bodyOutline" class="body-outline" />
        <ellipse cx="160" cy="48" rx="23" ry="28" class="body-outline" />
        <path
          v-if="view === 'front'"
          d="M153 61 Q160 65 167 61 M160 46 V54 M128 235 Q160 249 192 235 M138 358 Q143 363 150 358 M170 358 Q177 363 182 358"
          class="body-detail-line"
        />
        <path
          v-else
          d="M160 93 V232 M126 251 Q144 241 160 262 Q176 241 194 251 M160 262 V277 M137 362 H150 M170 362 H183"
          class="body-detail-line"
        />
      </g>

      <g
        v-for="region in regions"
        :key="`${view}-${region.id}`"
        class="muscle-map-region"
        :class="{ 'is-selected': selectedId === region.id }"
        role="button"
        tabindex="0"
        :aria-label="`人体${viewName}：${muscleName(region.id)}`"
        :aria-pressed="selectedId === region.id"
        :data-muscle="region.id"
        @click="emit('select', region.id)"
        @keydown.enter.prevent="emit('select', region.id)"
        @keydown.space.prevent="emit('select', region.id)"
        @pointerenter="hoveredId = region.id"
        @pointerleave="hoveredId = null"
        @focus="focusRegion(region.id)"
        @blur="focusedId = null"
      >
        <path
          v-for="(path, index) in region.paths"
          :key="`left-${index}`"
          :d="path"
          class="muscle-map-shape"
        />
        <g transform="translate(320 0) scale(-1 1)">
          <path
            v-for="(path, index) in region.paths"
            :key="`right-${index}`"
            :d="path"
            class="muscle-map-shape"
          />
        </g>
        <path :d="region.label.line" class="muscle-map-label-line" aria-hidden="true" />
        <text
          :x="region.label.x"
          :y="region.label.y"
          :text-anchor="region.label.anchor"
          class="muscle-map-label"
          aria-hidden="true"
        >
          {{ muscleName(region.id) }}
        </text>
      </g>
    </svg>

    <div class="body-map-status">
      <p role="status" aria-live="polite" aria-atomic="true">{{ selectionMessage }}</p>
      <button
        v-if="selectedId && !selectedIsVisible"
        type="button"
        class="show-selected-muscle"
        @click="showSelectedMuscle"
      >
        显示已选部位
      </button>
    </div>
    <p class="body-map-note">简化肌群示意图 · 也可使用部位按钮选择</p>
  </div>
</template>
