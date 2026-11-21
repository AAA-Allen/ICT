<script setup lang="ts">
import MuscleIcon from './MuscleIcon.vue'
import type { MuscleGroup, MuscleId } from '../types/muscle'

// 父组件传入列表与当前选择；子组件不直接修改父组件的数据。
defineProps<{
  muscles: MuscleGroup[]
  selectedId: MuscleId | null
}>()

// 点击后只通知父组件「用户选择了哪个 ID」。
const emit = defineEmits<{
  select: [muscleId: MuscleId]
}>()
</script>

<template>
  <div class="muscle-grid" role="group" aria-label="目标肌群，单选">
    <button
      v-for="(muscle, index) in muscles"
      :key="muscle.id"
      type="button"
      class="muscle-option"
      :class="{ 'is-selected': selectedId === muscle.id }"
      :aria-pressed="selectedId === muscle.id"
      :aria-label="`选择${muscle.name}`"
      @click="emit('select', muscle.id)"
    >
      <span class="option-topline">
        <span class="option-number">{{ String(index + 1).padStart(2, '0') }}</span>
        <span class="selection-indicator" aria-hidden="true">
          <svg v-if="selectedId === muscle.id" viewBox="0 0 16 16" fill="none">
            <path
              d="m4 8 3 3 5-6"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </span>
      </span>
      <MuscleIcon :muscle-id="muscle.icon" />
      <span class="option-name">{{ muscle.name }}</span>
      <span class="option-english">{{ muscle.englishName }}</span>
    </button>
  </div>
</template>
