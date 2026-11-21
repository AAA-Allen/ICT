<script setup lang="ts">
import { computed } from 'vue'
import { equipmentOptions, goalOptions, levelOptions } from '../data/trainingOptions'
import type { Equipment, TrainingGoal, TrainingLevel, TrainingPreferences } from '../types/training'

const props = defineProps<{
  modelValue: TrainingPreferences
  muscleName: string | null
}>()

const emit = defineEmits<{
  'update:modelValue': [preferences: TrainingPreferences]
  confirm: []
  reset: []
}>()

// 新建对象通知父组件，避免直接修改 props.modelValue 里的属性。
function updatePreferences(patch: Partial<TrainingPreferences>) {
  emit('update:modelValue', { ...props.modelValue, ...patch })
}

// 可写 computed 将原生下拉框的 v-model 转成组件的更新事件。
const goal = computed({
  get: () => props.modelValue.goal,
  set: (value: TrainingGoal) => updatePreferences({ goal: value }),
})

const level = computed({
  get: () => props.modelValue.level,
  set: (value: TrainingLevel) => updatePreferences({ level: value }),
})

const equipment = computed({
  get: () => props.modelValue.equipment,
  set: (value: Equipment) => updatePreferences({ equipment: value }),
})
</script>

<template>
  <form
    class="preferences-form"
    aria-labelledby="training-config-title"
    @submit.prevent="emit('confirm')"
  >
    <div class="section-heading">
      <div>
        <p class="eyebrow section-eyebrow">MAKE IT PERSONAL</p>
        <h2 id="training-config-title">让训练更适合你</h2>
      </div>
      <button type="button" class="reset-button" @click="emit('reset')">恢复默认</button>
    </div>
    <p class="section-description">设置你的目标与条件，配置概览会实时同步。</p>

    <div class="preference-fields">
      <div class="preference-field">
        <label for="training-goal">训练目标</label>
        <select id="training-goal" v-model="goal" name="goal">
          <option v-for="option in goalOptions" :key="option.value" :value="option.value">
            {{ option.label }}
          </option>
        </select>
      </div>
      <div class="preference-field">
        <label for="training-level">训练经验</label>
        <select id="training-level" v-model="level" name="level">
          <option v-for="option in levelOptions" :key="option.value" :value="option.value">
            {{ option.label }}
          </option>
        </select>
      </div>
      <div class="preference-field">
        <label for="training-equipment">可用器械</label>
        <select id="training-equipment" v-model="equipment" name="equipment">
          <option v-for="option in equipmentOptions" :key="option.value" :value="option.value">
            {{ option.label }}
          </option>
        </select>
      </div>
    </div>

    <div class="preferences-actions">
      <p id="confirm-hint" class="preferences-hint">
        {{
          muscleName
            ? `本次目标：${muscleName}，确认后可查看示例动作。`
            : '请先在上方选择目标肌群，再确认配置。'
        }}
      </p>
      <button
        type="submit"
        class="confirm-button"
        :disabled="!muscleName"
        aria-describedby="confirm-hint"
      >
        确认训练配置 <span aria-hidden="true">↗</span>
      </button>
    </div>
  </form>
</template>
