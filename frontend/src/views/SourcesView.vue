<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import dayjs from 'dayjs'
import { getSources } from '../api/client'
import type { SourceItem } from '../types/api'

const loading = ref(false)
const error = ref('')
const sources = ref<SourceItem[]>([])

const cnSources = computed(() => sources.value.filter((item) => item.region === 'cn'))
const intlSources = computed(() => sources.value.filter((item) => item.region === 'intl'))

function formatTime(value: string | null): string {
  if (!value) return '-'
  return dayjs(value).format('YYYY-MM-DD HH:mm')
}

function statusType(status: string): 'success' | 'info' | 'warning' | 'danger' {
  if (status === 'ok') return 'success'
  if (status === 'skipped') return 'info'
  if (status === 'error') return 'danger'
  return 'warning'
}

async function loadSources() {
  loading.value = true
  error.value = ''
  try {
    sources.value = await getSources()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(loadSources)
</script>

<template>
  <section class="page">
    <header>
      <h1>采集来源</h1>
      <p>中文源直连；国外 RSS 经本机代理。财经日历与热搜采集后写入本地；FinancialJuice 只直读内网库，不入库。</p>
    </header>

    <el-skeleton v-if="loading" :rows="8" animated />
    <el-result v-else-if="error" icon="error" :title="error">
      <template #extra>
        <el-button @click="loadSources">重试</el-button>
      </template>
    </el-result>
    <template v-else>
      <h2>中文源</h2>
      <el-table :data="cnSources" border>
        <el-table-column prop="name" label="名称" width="160" />
        <el-table-column prop="access" label="通道" width="100" />
        <el-table-column label="可运行" width="90">
          <template #default="{ row }">{{ row.runnable ? '是' : '否' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.last_status)">{{ row.last_status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最近采集" width="170">
          <template #default="{ row }">{{ formatTime(row.last_fetch_at) }}</template>
        </el-table-column>
        <el-table-column prop="last_count" label="新增" width="80" />
        <el-table-column prop="last_error" label="说明" min-width="220" show-overflow-tooltip />
      </el-table>

      <h2>国外源</h2>
      <el-table :data="intlSources" border>
        <el-table-column prop="name" label="名称" width="180" />
        <el-table-column prop="access" label="通道" width="100" />
        <el-table-column label="可运行" width="90">
          <template #default="{ row }">{{ row.runnable ? '是' : '否' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.last_status)">{{ row.last_status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最近采集" width="170">
          <template #default="{ row }">{{ formatTime(row.last_fetch_at) }}</template>
        </el-table-column>
        <el-table-column prop="last_count" label="新增" width="80" />
        <el-table-column prop="last_error" label="说明" min-width="220" show-overflow-tooltip />
      </el-table>
    </template>
  </section>
</template>

<style scoped>
.page {
  max-width: 1100px;
}

h1 {
  margin: 0 0 8px;
  font-size: 24px;
}

h2 {
  margin: 24px 0 12px;
  font-size: 16px;
}

p {
  margin: 0 0 16px;
  color: var(--muted);
}
</style>
