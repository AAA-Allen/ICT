<script setup lang="ts">
import { ref } from 'vue'
import MuscleMap from './MuscleMap.vue'
import MuscleModel3D from './MuscleModel3D.vue'
import type { MuscleGroup, MuscleId } from '../types/muscle'
import type { BodyView } from '../types/muscleMap'
import type { BodyModelStyle } from '../types/bodyModel'

defineProps<{
  muscles: MuscleGroup[]
  selectedId: MuscleId | null
  view: BodyView
  focusRequest: number
}>()
const emit = defineEmits<{ select: [id: MuscleId]; 'update:view': [view: BodyView] }>()
const mode = ref<'3d' | '2d'>('3d')
const modelStyle = ref<BodyModelStyle>('human')
const unavailable = ref(false)
function useFallback() {
  unavailable.value = true
  mode.value = '2d'
}
function enable3D() {
  unavailable.value = false
  mode.value = '3d'
}
</script>

<template>
  <div class="muscle-explorer">
    <div class="model-mode-switcher" role="group" aria-label="人体展示方式">
      <button type="button" :aria-pressed="mode === '3d'" @click="enable3D">3D 人体</button>
      <button type="button" :aria-pressed="mode === '2d'" @click="mode = '2d'">二维示意图</button>
    </div>
    <p v-if="unavailable" class="model-fallback-message" role="status">
      3D 暂时无法显示，已切换为二维图。肌群选择仍可使用；可点击“3D 人体”重试。
    </p>
    <div v-if="mode === '3d'" class="model-style-switcher" role="group" aria-label="3D 外观风格">
      <button type="button" :aria-pressed="modelStyle === 'human'" @click="modelStyle = 'human'">
        <span class="style-preview style-preview-human" aria-hidden="true">◯</span>
        <span><strong>人像模式</strong><small>修长比例 · 肌群分色</small></span>
      </button>
      <button type="button" :aria-pressed="modelStyle === 'cute'" @click="modelStyle = 'cute'">
        <span class="style-preview style-preview-cute" aria-hidden="true">•ᴗ•</span>
        <span><strong>呆萌模式</strong><small>大头圆脸 · 软萌伙伴</small></span>
      </button>
    </div>
    <MuscleModel3D
      v-if="mode === '3d'"
      :muscles="muscles"
      :selected-id="selectedId"
      :focus-request="focusRequest"
      :model-style="modelStyle"
      @select="emit('select', $event)"
      @unavailable="useFallback"
    />
    <MuscleMap
      v-else
      :muscles="muscles"
      :selected-id="selectedId"
      :view="view"
      @select="emit('select', $event)"
      @update:view="emit('update:view', $event)"
    />
  </div>
</template>
