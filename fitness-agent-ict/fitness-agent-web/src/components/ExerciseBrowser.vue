<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import ExerciseCard from './ExerciseCard.vue'
import { exerciseDifficulties } from '../data/exerciseOptions'
import { getLocalExercises } from '../services/exerciseService'
import type { TrainingConfiguration, TrainingLevel } from '../types/training'

const props = defineProps<{
  configuration: TrainingConfiguration | null
  confirmed: boolean
  selectedExerciseId: string | null
}>()
const emit = defineEmits<{ 'update:selectedExerciseId': [exerciseId: string | null] }>()

// 搜索与难度在模块内保存，动作选择由父页面共享给视频区域。
const keyword = ref('')
const difficulty = ref<TrainingLevel | 'all'>('all')
const canBrowse = computed(() => props.confirmed && props.configuration !== null)

const matchingExercises = computed(() =>
  canBrowse.value && props.configuration ? getLocalExercises(props.configuration) : [],
)

const visibleExercises = computed(() => {
  const query = keyword.value.trim().toLocaleLowerCase()
  return matchingExercises.value.filter(
    (exercise) =>
      (difficulty.value === 'all' || exercise.difficulty === difficulty.value) &&
      exercise.name.toLocaleLowerCase().includes(query),
  )
})

// 动作选择与搜索结果独立。暂时搜索不到已选动作，不应悄悄丢失选择。
const selectedExercise = computed(
  () =>
    matchingExercises.value.find((exercise) => exercise.id === props.selectedExerciseId) ?? null,
)

function clearFilters() {
  keyword.value = ''
  difficulty.value = 'all'
}

function selectExercise(exerciseId: string) {
  emit('update:selectedExerciseId', exerciseId)
}

// 训练条件变化时清空旧搜索；共享的动作选择由父页面统一清理。
watch([() => props.configuration, () => props.confirmed], () => {
  clearFilters()
})
</script>

<template>
  <section class="exercise-browser" aria-labelledby="exercise-browser-title">
    <div class="section-heading">
      <div>
        <p class="eyebrow section-eyebrow">EXPLORE YOUR NEXT MOVE</p>
        <h2 id="exercise-browser-title">探索你的下一项练习</h2>
      </div>
      <span class="local-only-tag">本地示例库</span>
    </div>
    <p class="section-description">
      按配置筛选示例动作，用于功能演示。正式推荐将在接入 Agent 后提供。
    </p>

    <div v-if="!canBrowse" class="exercise-empty exercise-waiting" role="status">
      <span class="exercise-empty-symbol" aria-hidden="true">01 → 02</span>
      <h3>先确认你的训练配置</h3>
      <p>在上方选择肌群和训练条件，再点击“确认训练配置”，即可查看匹配的示例动作。</p>
    </div>

    <template v-else>
      <div class="exercise-toolbar">
        <div class="exercise-search">
          <label for="exercise-keyword">搜索动作名称</label>
          <input
            id="exercise-keyword"
            v-model="keyword"
            type="search"
            placeholder="例如：深蹲、哑铃"
          />
        </div>
        <div class="exercise-filter">
          <label for="exercise-difficulty">动作难度</label>
          <select id="exercise-difficulty" v-model="difficulty">
            <option value="all">全部难度</option>
            <option
              v-for="option in exerciseDifficulties"
              :key="option.value"
              :value="option.value"
            >
              {{ option.label }}
            </option>
          </select>
        </div>
        <button
          class="reset-button clear-exercise-filters"
          type="button"
          :disabled="!keyword && difficulty === 'all'"
          @click="clearFilters"
        >
          清除筛选
        </button>
      </div>

      <p class="exercise-result-count" role="status" aria-live="polite">
        显示 {{ visibleExercises.length }} / {{ matchingExercises.length }} 个匹配动作
      </p>

      <div v-if="matchingExercises.length === 0" class="exercise-empty exercise-no-match">
        <h3>当前配置没有匹配的示例动作</h3>
        <p>示例库的覆盖范围有限。你可以在上方调整目标、经验或器械，再重新确认配置。</p>
      </div>
      <div v-else-if="visibleExercises.length === 0" class="exercise-empty exercise-no-results">
        <h3>没有符合搜索或难度条件的动作</h3>
        <p>当前配置有 {{ matchingExercises.length }} 个匹配动作，清除筛选即可重新查看。</p>
        <button type="button" class="empty-filter-button" @click="clearFilters">
          清除筛选并查看全部
        </button>
      </div>
      <div v-else class="exercise-grid">
        <ExerciseCard
          v-for="exercise in visibleExercises"
          :key="exercise.id"
          :exercise="exercise"
          :selected="selectedExerciseId === exercise.id"
          @select="selectExercise"
        />
      </div>

      <div class="exercise-selection-bar">
        <div role="status" aria-live="polite">
          <p class="exercise-selection-name">
            {{ selectedExercise ? `本次选择：${selectedExercise.name}` : '尚未选择动作' }}
          </p>
          <p class="exercise-selection-note">
            {{
              selectedExercise
                ? '已保留本次动作选择，修改训练配置时会清空。'
                : '选择一张卡片，作为本次准备了解的动作。'
            }}
          </p>
        </div>
        <div class="exercise-selection-actions">
          <a v-if="selectedExercise" href="#video-upload-title" class="continue-video-link"
            >继续选择视频 ↗</a
          >
          <button
            type="button"
            class="reset-button clear-exercise-selection"
            :disabled="!selectedExercise"
            @click="emit('update:selectedExerciseId', null)"
          >
            清除动作选择
          </button>
        </div>
      </div>
    </template>
  </section>
</template>
