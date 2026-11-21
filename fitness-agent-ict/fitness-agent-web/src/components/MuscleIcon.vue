<script setup lang="ts">
import type { MuscleIconKind } from '../types/muscle'

defineProps<{ muscleId: MuscleIconKind }>()
</script>

<template>
  <!-- 用简单线条区分按钮；这些图标不是可点击的人体解剖图。 -->
  <svg viewBox="0 0 64 64" fill="none" aria-hidden="true" class="muscle-icon">
    <g stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
      <template v-if="muscleId === 'chest'">
        <path d="M24 11v7l-12 5 5 29h30l5-29-12-5v-7" />
        <path d="M32 24v20M16 28c5-6 11-5 16-2 5-3 11-4 16 2l-4 12c-5 2-9 1-12-2-3 3-7 4-12 2z" />
      </template>
      <template v-else-if="muscleId === 'back'">
        <path d="M24 11v7l-12 5 5 29h30l5-29-12-5v-7M32 20v30" />
        <path d="m17 28 10 6 3 12M47 28l-10 6-3 12M24 19l8 8 8-8" />
      </template>
      <template v-else-if="muscleId === 'shoulders'">
        <path d="M24 11v7l-9 4-6 11 5 16 6-2-2-15M40 11v7l9 4 6 11-5 16-6-2 2-15M20 32v20h24V32" />
        <path d="M24 19c-9 0-13 5-11 14l7-2 6-7M40 19c9 0 13 5 11 14l-7-2-6-7" />
      </template>
      <template v-else-if="muscleId === 'arms'">
        <path d="m18 47 7-19 9-7 11 3 7 15-9 10H18zM25 28l-3-12 6-6 7 3-1 8" />
        <path d="m28 13-1 5 7 3M26 39c5-9 14-10 19-4M20 47l-4 6h25l4-4" />
      </template>
      <template v-else-if="muscleId === 'abs'">
        <path d="m20 11-5 13 5 29h24l5-29-5-13M32 20v29" />
        <path d="M24 23h16v8H24zM25 35h14v8H25zM20 28l-4-3M44 28l4-3M21 40l-3-2M43 40l3-2" />
      </template>
      <template v-else>
        <path d="M20 10h24l3 17-7 27h-8V34h-2v20h-8l-5-27z" />
        <path d="m20 17 9 5 3 12 3-12 9-5M21 27l5 8-1 11M43 27l-5 8 1 11" />
      </template>
    </g>
  </svg>
</template>
