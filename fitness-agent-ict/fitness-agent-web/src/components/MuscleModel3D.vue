<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { MuscleGroup, MuscleId } from '../types/muscle'
import type { CameraState, MuscleScene } from '../three/createMuscleScene'
import type { BodyModelStyle } from '../types/bodyModel'

const props = defineProps<{
  muscles: MuscleGroup[]
  selectedId: MuscleId | null
  focusRequest: number
  modelStyle: BodyModelStyle
}>()
const emit = defineEmits<{ select: [id: MuscleId]; unavailable: [] }>()
const canvas = ref<HTMLCanvasElement | null>(null)
const loading = ref(true)
const hoveredId = ref<MuscleId | null>(null)
const cameraState = ref<CameraState>({
  view: 'free',
  angle: 0,
  distance: 0,
  canZoomIn: true,
  canZoomOut: true,
})
let scene: MuscleScene | undefined
let unmounted = false

const selectedName = computed(
  () => props.muscles.find((muscle) => muscle.id === props.selectedId)?.name,
)
const hoverName = computed(
  () => props.muscles.find((muscle) => muscle.id === hoveredId.value)?.name,
)
const viewLabel = computed(
  () => ({ front: '正面', back: '背面', free: '自由视角' })[cameraState.value.view],
)

onMounted(async () => {
  try {
    // 独立加载 3D 引擎；快捷按钮和二维图不依赖这个下载完成。
    const { createMuscleScene } = await import('../three/createMuscleScene')
    if (unmounted || !canvas.value) return
    scene = createMuscleScene(
      canvas.value,
      {
        select: (id) => emit('select', id),
        hover: (id) => {
          hoveredId.value = id
        },
        camera: (state) => {
          cameraState.value = state
        },
        error: () => emit('unavailable'),
      },
      props.modelStyle,
    )
    scene.setSelection(props.selectedId)
    if (props.selectedId) scene.focus(props.selectedId)
    loading.value = false
  } catch {
    if (!unmounted) emit('unavailable')
  }
})

watch(
  () => props.selectedId,
  (id) => scene?.setSelection(id),
)
watch(
  () => props.modelStyle,
  (style) => {
    hoveredId.value = null
    scene?.setStyle(style)
  },
)
// 只有快捷按钮请求才定位视角。直接点击模型不跳转相机，重复点同一按钮仍可定位。
watch(
  () => props.focusRequest,
  () => {
    if (props.selectedId) scene?.focus(props.selectedId)
  },
)
onBeforeUnmount(() => {
  unmounted = true
  scene?.dispose()
})
</script>

<template>
  <div class="model-3d" :class="`model-style-${modelStyle}`" :aria-busy="loading">
    <div class="model-view-controls" role="group" aria-label="3D 人体视角">
      <button
        type="button"
        :disabled="loading"
        :aria-pressed="cameraState.view === 'front'"
        @click="scene?.setView('front')"
      >
        正面
      </button>
      <button
        type="button"
        :disabled="loading"
        :aria-pressed="cameraState.view === 'back'"
        @click="scene?.setView('back')"
      >
        背面
      </button>
      <button type="button" :disabled="loading" class="model-reset" @click="scene?.reset()">
        恢复视角
      </button>
    </div>
    <div class="model-stage">
      <div class="model-stage-caption" aria-hidden="true">
        <span>{{ modelStyle === 'human' ? 'HUMAN ANATOMY' : 'LITTLE TRAINING BUDDY' }}</span
        ><span>360°</span>
      </div>
      <canvas
        ref="canvas"
        class="model-canvas"
        tabindex="0"
        role="img"
        aria-label="可旋转的 3D 人体肌群模型"
        aria-describedby="model-instructions"
        :data-view="cameraState.view"
        :data-angle="cameraState.angle.toFixed(4)"
        :data-distance="cameraState.distance.toFixed(4)"
        :data-selected="selectedId ?? ''"
        :data-style="modelStyle"
        >可通过下方或旁边的 11 个肌群按钮选择部位。</canvas
      >
      <div v-if="loading" class="model-loading" role="status">正在准备 3D 人体…</div>
      <div v-else class="model-stage-footer">
        <span class="model-angle-label">{{ viewLabel }}</span>
        <div class="model-zoom" role="group" aria-label="模型缩放">
          <button
            type="button"
            aria-label="缩小人体"
            :disabled="!cameraState.canZoomOut"
            @click="scene?.zoom(1.15)"
          >
            −
          </button>
          <button
            type="button"
            aria-label="放大人体"
            :disabled="!cameraState.canZoomIn"
            @click="scene?.zoom(1 / 1.15)"
          >
            +
          </button>
        </div>
      </div>
    </div>
    <div class="model-selection-status">
      <p role="status" aria-live="polite" aria-atomic="true">
        {{
          hoverName && hoveredId !== selectedId
            ? `${hoverName} · 点击选择`
            : selectedName
              ? `已选择：${selectedName}`
              : '点击彩色肌群，选择训练目标'
        }}
      </p>
      <button
        type="button"
        :disabled="!selectedId || loading"
        @click="selectedId && scene?.focus(selectedId)"
      >
        定位已选肌群 ↗
      </button>
    </div>
    <p id="model-instructions" class="model-instructions">
      拖动旋转 · 滚轮 / 双指缩放<br />键盘 ← → 旋转，+ − 缩放，Home 恢复；使用肌群按钮选取。
    </p>
    <p class="body-map-note">
      {{
        modelStyle === 'human' ? '人像模式 · 修长比例与肌群分色' : '呆萌模式 · 大头比例与圆润轮廓'
      }}
      · 11 个可选肌群
    </p>
  </div>
</template>
