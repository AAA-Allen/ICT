<script setup lang="ts">
import { onBeforeUnmount } from 'vue'
import {
  cancelCatalogLoad,
  catalogError,
  catalogLoadedAt,
  catalogLoading,
  catalogSource,
  catalogSourceLabel,
  loadJavaCatalog,
  queryCatalog,
  useLocalCatalog,
} from '../services/catalogSource'
onBeforeUnmount(cancelCatalogLoad)
</script>

<template>
  <section
    class="query-source-panel"
    aria-labelledby="query-source-title"
    :data-source="catalogSource"
    :aria-busy="catalogLoading"
  >
    <div class="query-source-heading">
      <h2 id="query-source-title">资料查询来源</h2>
      <span class="query-source-badge">{{ catalogSourceLabel }}</span>
    </div>
    <p>
      可读取本机 Java
      查询服务的资料快照，页面筛选使用这份资料。读取资料与徒手深蹲的视频上传分别操作。
    </p>
    <div class="query-source-actions">
      <button
        type="button"
        class="use-local-catalog"
        :aria-pressed="catalogSource === 'local'"
        @click="useLocalCatalog"
      >
        使用前端本地资料</button
      ><button
        type="button"
        class="connect-java-button"
        :disabled="catalogLoading"
        :aria-pressed="catalogSource === 'java'"
        @click="loadJavaCatalog"
      >
        {{
          catalogLoading
            ? '正在读取 Java 资料…'
            : catalogSource === 'java'
              ? '重新读取 Java 资料'
              : '读取 Java 查询服务'
        }}
      </button>
    </div>
    <p class="query-source-status" role="status">
      {{
        catalogLoading
          ? '正在读取，当前资料继续可用；可点击本地资料取消。'
          : `当前：${catalogSourceLabel} · ${queryCatalog.muscles.length} 个肌群 / ${queryCatalog.exercises.length} 个动作 / ${queryCatalog.demonstrations.length} 条示范资料`
      }}<span v-if="catalogSource === 'java' && catalogLoadedAt">
        · 读取于 {{ catalogLoadedAt }}</span
      >
    </p>
    <p v-if="catalogError" class="query-source-error" role="status">
      {{ catalogError }} 当前继续使用{{ catalogSourceLabel
      }}{{ catalogSource === 'java' ? '（上次已读取的内容）' : '' }}，可重试或切回本地资料。
    </p>
    <p class="query-source-hint">
      默认地址 127.0.0.1:8081，通过本地开发代理读取。独立个人协议
      personal-v1，团队接口统一留待最后适配。
    </p>
  </section>
</template>
