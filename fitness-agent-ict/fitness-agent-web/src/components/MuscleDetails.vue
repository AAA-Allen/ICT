<script setup lang="ts">
import MuscleIcon from './MuscleIcon.vue'
import type { MuscleGroup } from '../types/muscle'

defineProps<{ muscle: MuscleGroup | null }>()
</script>

<template>
  <aside class="selection-panel" aria-labelledby="selection-title">
    <div class="panel-topline">
      <span class="eyebrow">YOUR FOCUS</span>
      <span class="panel-status">{{ muscle ? '已选择' : '等待选择' }}</span>
    </div>

    <div class="focus-art" aria-hidden="true">
      <div class="orbit orbit-one" />
      <div class="orbit orbit-two" />
      <div class="focus-icon-wrap">
        <MuscleIcon v-if="muscle" :muscle-id="muscle.icon" />
        <svg v-else viewBox="0 0 64 64" fill="none" class="muscle-icon">
          <circle cx="32" cy="32" r="17" stroke="currentColor" stroke-width="1.5" />
          <circle cx="32" cy="32" r="8" stroke="currentColor" stroke-width="1.5" />
          <path
            d="M32 7v11m0 28v11M7 32h11m28 0h11"
            stroke="currentColor"
            stroke-width="1.5"
            stroke-linecap="round"
          />
        </svg>
      </div>
      <span class="art-spark spark-one" />
      <span class="art-spark spark-two" />
    </div>

    <div class="selection-copy" role="status" aria-live="polite" aria-atomic="true">
      <template v-if="muscle">
        <p class="selection-kicker">当前选择 · {{ muscle.englishName }}</p>
        <h3 id="selection-title">{{ muscle.name }}<span class="title-dot">.</span></h3>
        <p class="selection-description">{{ muscle.description }}</p>
        <div class="muscle-tags-block">
          <p class="tags-label">主要肌肉</p>
          <ul class="muscle-tags" aria-label="主要肌肉">
            <li v-for="name in muscle.primaryMuscles" :key="name">{{ name }}</li>
          </ul>
        </div>
      </template>
      <template v-else>
        <p class="selection-kicker">你的训练，由你定义</p>
        <h3 id="selection-title">今天，练哪里<span class="title-dot">?</span></h3>
        <p class="selection-description">
          点击左侧任意肌群，查看对应部位的信息。一个清晰的目标，是开始的第一步。
        </p>
        <div class="empty-selection-note">
          <span aria-hidden="true">↖</span> 先选一个你想了解的部位
        </div>
      </template>
    </div>

    <div class="panel-bottom">
      <span class="panel-bottom-icon" aria-hidden="true">↗</span>
      <div>
        <p>选择目标，是训练的起点</p>
        <span>动作推荐与视频分析将在后续阶段接入</span>
      </div>
    </div>
  </aside>
</template>
