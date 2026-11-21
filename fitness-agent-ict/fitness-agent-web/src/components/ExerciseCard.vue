<script setup lang="ts">
import { computed } from 'vue'
import MuscleIcon from './MuscleIcon.vue'
import { exerciseDifficulties } from '../data/exerciseOptions'
import { muscles } from '../data/muscles'
import { equipmentOptions, goalOptions } from '../data/trainingOptions'
import type { Exercise } from '../types/exercise'

const props = defineProps<{ exercise: Exercise; selected: boolean }>()
const emit = defineEmits<{ select: [exerciseId: string] }>()

const muscleIcon = computed(
  () => muscles.find((muscle) => muscle.id === props.exercise.targetMuscles[0])?.icon ?? 'chest',
)

const difficultyLabel = computed(
  () => exerciseDifficulties.find((option) => option.value === props.exercise.difficulty)?.label,
)
const equipmentLabel = computed(
  () => equipmentOptions.find((option) => option.value === props.exercise.equipment)?.label,
)
const muscleNames = computed(() =>
  props.exercise.targetMuscles
    .map((id) => muscles.find((muscle) => muscle.id === id)?.name)
    .join(' / '),
)
const goalNames = computed(() =>
  props.exercise.goals
    .map((goal) => goalOptions.find((option) => option.value === goal)?.label)
    .join(' / '),
)
</script>

<template>
  <article class="exercise-card" :class="{ 'is-selected': selected }" :aria-label="exercise.name">
    <div class="exercise-card-top">
      <span class="exercise-card-icon"><MuscleIcon :muscle-id="muscleIcon" /></span>
      <span class="exercise-difficulty">{{ difficultyLabel }}</span>
    </div>
    <h3>{{ exercise.name }}</h3>
    <p class="exercise-muscles">{{ muscleNames }}</p>
    <p class="exercise-equipment">所需器械：{{ equipmentLabel }}</p>
    <details class="exercise-details">
      <summary>查看动作资料</summary>
      <p>{{ exercise.description }}</p>
      <p>示例目标标签：{{ goalNames }}</p>
      <p>示范视频尚未接入。</p>
    </details>
    <button
      class="exercise-select-button"
      type="button"
      :aria-pressed="selected"
      :aria-label="`选择动作：${exercise.name}`"
      @click="emit('select', exercise.id)"
    >
      {{ selected ? '已选择此动作' : '选择此动作'
      }}<span aria-hidden="true">{{ selected ? '✓' : '↗' }}</span>
    </button>
  </article>
</template>
