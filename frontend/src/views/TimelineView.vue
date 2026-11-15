<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getNews, getSources, triggerIngest } from '../api/client'
import { useNewsSocket } from '../composables/useNewsSocket'
import type { Article, SourceItem } from '../types/api'
import { formatTime, stripHtml } from '../utils/text'

const loading = ref(false)
const ingesting = ref(false)
const error = ref('')
const keyword = ref('')
const sourceId = ref('')
const lang = ref('')
const level = ref('')
const page = ref(1)
const pageSize = 20
const total = ref(0)
const articles = ref<Article[]>([])
const sources = ref<SourceItem[]>([])

async function loadSources() {
  sources.value = await getSources()
}

async function loadNews(opts?: { silent?: boolean }) {
  const silent = opts?.silent === true
  if (!silent) {
    loading.value = true
    error.value = ''
  }
  try {
    const data = await getNews({
      page: page.value,
      page_size: pageSize,
      source_id: sourceId.value || undefined,
      q: keyword.value.trim() || undefined,
      lang: lang.value || undefined,
      level: level.value || undefined,
    })
    articles.value = data.items
    total.value = data.total
    error.value = ''
  } catch (err) {
    if (!silent) {
      error.value = err instanceof Error ? err.message : '加载失败'
    }
  } finally {
    if (!silent) {
      loading.value = false
    }
  }
}

async function runIngest() {
  ingesting.value = true
  try {
    const result = await triggerIngest()
    if (result.running) {
      ElMessage.warning('已有采集在按顺序进行，请稍后再试')
      return
    }
    ElMessage.success(
      `采集完成：新增 ${result.inserted} 条，成功 ${result.fetched_sources} 个源` +
        (result.deferred_sources ? `，间隔未到跳过 ${result.deferred_sources} 个` : ''),
    )
    await loadNews()
    await loadSources()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '采集失败')
  } finally {
    ingesting.value = false
  }
}

function resetAndLoad() {
  page.value = 1
  void loadNews()
}

function onSourceChange() {
  if (sourceId.value && sourceId.value !== 'financialjuice') {
    level.value = ''
  }
  resetAndLoad()
}

function isCritical(value: string): boolean {
  return value.includes('critical')
}

useNewsSocket(() => {
  if (ingesting.value || page.value !== 1) {
    return
  }
  void loadNews({ silent: true })
})

onMounted(async () => {
  try {
    await loadSources()
    await loadNews()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  }
})
</script>

<template>
  <section class="page">
    <header class="header">
      <div>
        <h1>科技时间线</h1>
        <p>中文直连 RSS；国外源经本机代理；FinancialJuice 直读内网库。</p>
      </div>
      <el-button type="primary" :loading="ingesting" @click="runIngest">立即采集</el-button>
    </header>

    <div class="filters">
      <el-input v-model="keyword" placeholder="搜索标题" clearable style="width: 220px" @change="resetAndLoad" />
      <el-select v-model="lang" placeholder="全部语言" clearable style="width: 140px" @change="resetAndLoad">
        <el-option label="中文" value="zh" />
        <el-option label="英文" value="en" />
      </el-select>
      <el-select v-model="sourceId" placeholder="全部来源" clearable style="width: 200px" @change="onSourceChange">
        <el-option v-for="item in sources.filter((s) => s.kind === 'news')" :key="item.id" :label="item.name" :value="item.id" />
      </el-select>
      <el-select
        v-if="!sourceId || sourceId === 'financialjuice'"
        v-model="level"
        placeholder="全部级别"
        clearable
        style="width: 140px"
        @change="resetAndLoad"
      >
        <el-option label="重要新闻" value="active active-critical" />
        <el-option label="经济数据" value="active" />
      </el-select>
    </div>

    <el-skeleton v-if="loading" :rows="8" animated />
    <el-result v-else-if="error" icon="error" :title="error">
      <template #extra>
        <el-button @click="loadNews">重试</el-button>
      </template>
    </el-result>
    <el-empty v-else-if="articles.length === 0" description="暂无新闻" />
    <div v-else class="list">
      <article
        v-for="item in articles"
        :key="item.source_id + '-' + item.id"
        class="card"
        :class="{ 'tone-critical': isCritical(item.level) }"
      >
        <div class="meta">
          <span>{{ item.source_name }}</span>
          <span v-if="item.origin">{{ item.origin }}</span>
          <span>{{ formatTime(item.published_at || item.fetched_at) }}</span>
        </div>
        <a :href="item.url" target="_blank" rel="noreferrer">{{ item.title }}</a>
        <p v-if="item.summary">{{ stripHtml(item.summary).slice(0, 160) }}</p>
      </article>
      <div class="pager">
        <el-pagination
          background
          layout="prev, pager, next, total"
          :page-size="pageSize"
          :current-page="page"
          :total="total"
          @current-change="(value: number) => { page = value; loadNews() }"
        />
      </div>
    </div>
  </section>
</template>

<style scoped>
.page { max-width: 880px; }
.header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 20px; }
h1 { margin: 0 0 8px; font-size: 24px; }
p { margin: 0; color: var(--muted); }
.filters { display: flex; gap: 12px; margin-bottom: 16px; }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px 18px; margin-bottom: 10px; }
.meta { display: flex; gap: 12px; color: var(--muted); font-size: 12px; margin-bottom: 8px; flex-wrap: wrap; }
.card a { font-size: 16px; font-weight: 600; color: var(--text); }
.card.tone-critical { background: #fde8e8; border-color: #f5c2c0; }
.card p { margin-top: 8px; line-height: 1.6; }
.pager { display: flex; justify-content: flex-end; margin-top: 16px; }
</style>
