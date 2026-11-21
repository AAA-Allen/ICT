<script setup lang="ts">
import { computed } from 'vue'
import { equipmentOptions, goalOptions, levelOptions } from '../data/trainingOptions'
import type { MuscleGroup } from '../types/muscle'
import type { TrainingPreferences } from '../types/training'

const props = defineProps<{
  muscle: MuscleGroup | null
  preferences: TrainingPreferences
  confirmed: boolean
}>()

const summaryRows = computed(() => [
  { label: '目标肌群', value: props.muscle?.name ?? '尚未选择' },
  {
    label: '训练目标',
    value: goalOptions.find((option) => option.value === props.preferences.goal)?.label ?? '—',
  },
  {
    label: '训练经验',
    value: levelOptions.find((option) => option.value === props.preferences.level)?.label ?? '—',
  },
  {
    label: '可用器械',
    value:
      equipmentOptions.find((option) => option.value === props.preferences.equipment)?.label ?? '—',
  },
])
</script>

<template>
  <aside class="training-summary" aria-labelledby="training-summary-title">
    <div class="summary-heading">
      <h3 id="training-summary-title">本次配置概览</h3>
      <span class="local-only-tag">本地预览</span>
    </div>
    <dl class="training-summary-list">
      <div v-for="row in summaryRows" :key="row.label" class="training-summary-row">
        <dt>{{ row.label }}</dt>
        <dd>{{ row.value }}</dd>
      </div>
    </dl>
    <p
      class="configuration-status"
      :class="{ 'is-confirmed': confirmed }"
      role="status"
      aria-live="polite"
    >
      {{
        confirmed
          ? '配置已确认，可向下查看匹配的示例动作。'
          : muscle
            ? '配置尚未确认；修改条件后需要重新确认。'
            : '选择目标肌群后，即可确认本次配置。'
      }}
    </p>
    <p class="configuration-note">当前只在本页确认，尚未发送到服务器。刷新后恢复默认。</p>
  </aside>
</template>
