<script setup lang="ts">
import { RouterView, useRoute } from 'vue-router'

const route = useRoute()
const isOnlineTrial = import.meta.env.MODE === 'online'
function focusMain() {
  document.getElementById('main-content')?.focus()
}
</script>

<template>
  <a class="skip-link" href="#main-content" @click.prevent="focusMain">跳到主要内容</a>

  <header v-if="route.name !== 'live'" class="site-header">
    <div id="project-header" class="header-inner">
      <a
        class="brand"
        href="#main-content"
        aria-label="Fitness Agent，回到肌群选择"
        @click.prevent="focusMain"
      >
        <svg class="brand-mark" viewBox="0 0 40 40" fill="none" aria-hidden="true">
          <rect width="40" height="40" rx="12" fill="currentColor" />
          <path
            d="M9 21h6l4-10 5 19 4-9h4"
            stroke="#d9edb2"
            stroke-width="2.5"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
        <span>Fitness<span class="brand-light"> Agent</span></span>
      </a>
      <RouterLink class="live-link" to="/live">实时深蹲</RouterLink>
      <span v-if="!isOnlineTrial" class="prototype-badge">
        <span class="status-dot" />本地交互原型
      </span>
    </div>
  </header>

  <RouterView />

  <footer v-if="route.name !== 'live'" class="site-footer">
    <span>FITNESS AGENT <span class="footer-separator">/</span> 为每一次进步而设计</span>
    <span v-if="!isOnlineTrial">Day 11 · 个人模块演示版</span>
  </footer>
</template>
