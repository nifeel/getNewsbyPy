<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import dayjs from 'dayjs'
import { marked } from 'marked'
import { ElMessage } from 'element-plus'
import { generateSummary, getSummaries, getSummary } from '../api/client'
import type { SummaryDetail, SummaryItem } from '../types/api'

marked.setOptions({ gfm: true, breaks: true })

const loading = ref(false)
const generating = ref(false)
const error = ref('')
const items = ref<SummaryItem[]>([])
const current = ref('')
const detail = ref<SummaryDetail | null>(null)

const available = computed(() => new Set(items.value.map((item) => item.date)))

function disableDay(day: Date): boolean {
  return !available.value.has(dayjs(day).format('YYYY-MM-DD'))
}

const html = computed(() => {
  const markdown = detail.value?.markdown || ''
  if (!markdown) {
    return ''
  }
  return marked.parse(markdown, { async: false }) as string
})

async function loadList() {
  loading.value = true
  error.value = ''
  try {
    items.value = await getSummaries()
    if (!current.value && items.value.length > 0) {
      current.value = items.value[0].date
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
    items.value = []
  } finally {
    loading.value = false
  }
}

async function loadDetail(day: string) {
  if (!day) {
    detail.value = null
    return
  }
  loading.value = true
  error.value = ''
  try {
    detail.value = await getSummary(day)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
    detail.value = null
  } finally {
    loading.value = false
  }
}

watch(current, (day) => {
  void loadDetail(day)
})

async function handleGenerate() {
  generating.value = true
  try {
    const res = await generateSummary(undefined, true)
    ElMessage.success(`生成成功：${res.filename}`)
    await loadList()
    current.value = res.date
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '生成失败')
  } finally {
    generating.value = false
  }
}

onMounted(loadList)
</script>

<template>
  <section class="page">
    <header class="header">
      <div>
        <h1>报告</h1>
        <p>按业务日存放的 markdown 报告，每天 0 点由 AI (CLIProxyAPI) 自动生成，亦支持手动生成与回溯。</p>
      </div>
      <el-button :loading="generating" @click="handleGenerate">生成昨日日报</el-button>
    </header>

    <div v-if="items.length" class="filters">
      <el-date-picker
        v-model="current"
        class="filter-item"
        type="date"
        value-format="YYYY-MM-DD"
        placeholder="选择日期"
        :clearable="false"
        :disabled-date="disableDay"
      />
    </div>

    <el-skeleton v-if="loading && !detail" :rows="8" animated />
    <el-result v-else-if="error" icon="error" :title="error">
      <template #extra>
        <el-button @click="loadList">重试</el-button>
      </template>
    </el-result>
    <el-empty v-else-if="items.length === 0" description="还没有报告。用 Cursor 生成 YYYY-MM-DD.md 放到 backend/data/summaries/" />
    <article v-else-if="detail" class="body" v-html="html" />
  </section>
</template>

<style scoped>
.page {
  max-width: 880px;
}

.header {
  margin-bottom: 16px;
}

h1 {
  margin: 0 0 8px;
  font-size: 24px;
}

.filters {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}

.filter-item {
  width: 180px;
}

.body {
  background: var(--panel);
  border: 1px solid var(--line);
  padding: 20px 22px 28px;
  line-height: 1.7;
}

.body :deep(h1),
.body :deep(h2),
.body :deep(h3) {
  color: var(--text);
  margin: 1.2em 0 0.5em;
}

.body :deep(h1) {
  margin-top: 0;
  font-size: 22px;
}

.body :deep(h2) {
  font-size: 16px;
}

.body :deep(p),
.body :deep(li) {
  color: var(--text);
}

.body :deep(a) {
  color: var(--accent);
}

.body :deep(ul),
.body :deep(ol) {
  padding-left: 1.4em;
}
</style>
