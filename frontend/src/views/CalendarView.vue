<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import dayjs from 'dayjs'
import { ElMessage } from 'element-plus'
import { getEvents, syncEvents } from '../api/client'
import type { EventItem } from '../types/api'

const loading = ref(false)
const error = ref('')
const events = ref<EventItem[]>([])
const month = ref('')
const kind = ref<'all' | 'tech' | 'econ'>('all')

const monthEvents = computed(() => {
  const today = dayjs().format('YYYY-MM-DD')
  return events.value.filter((item) => {
    if (!month.value && item.start_date < today) return false
    if (month.value && !item.start_date.startsWith(month.value)) return false
    if (kind.value === 'econ') return item.category === 'econ'
    if (kind.value === 'tech') return item.category !== 'econ'
    return true
  })
})

const statusLabel: Record<string, string> = {
  confirmed: '已确认',
  estimated: '预计窗口',
}

function stamp(item: EventItem) {
  if (item.time) return `${item.start_date} ${item.time}`
  return `${item.start_date}${item.end_date ? ' ~ ' + item.end_date : ''}`
}

async function loadEvents() {
  loading.value = true
  error.value = ''
  try {
    events.value = await getEvents()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function refreshSeeds() {
  try {
    const result = await syncEvents()
    ElMessage.success(`已同步 ${result.upserted} 条国内事件`)
    await loadEvents()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '同步失败')
  }
}

onMounted(loadEvents)
</script>

<template>
  <section class="page">
    <header class="header">
      <div>
        <h1>大事件</h1>
        <p>科技会议与财经日历写入本地。财经源按周累积；FinancialJuice 新闻仍直读不入库。</p>
      </div>
      <el-button @click="refreshSeeds">同步种子</el-button>
    </header>

    <div class="filters">
      <el-radio-group v-model="kind">
        <el-radio-button value="all">全部</el-radio-button>
        <el-radio-button value="tech">科技</el-radio-button>
        <el-radio-button value="econ">财经</el-radio-button>
      </el-radio-group>
      <el-date-picker
        v-model="month"
        type="month"
        value-format="YYYY-MM"
        placeholder="全部即将到来"
        clearable
      />
    </div>

    <el-skeleton v-if="loading" :rows="6" animated />
    <el-result v-else-if="error" icon="error" :title="error">
      <template #extra>
        <el-button @click="loadEvents">重试</el-button>
      </template>
    </el-result>
    <el-empty v-else-if="monthEvents.length === 0" description="该筛选下暂无事件" />
    <el-timeline v-else class="timeline">
      <el-timeline-item v-for="item in monthEvents" :key="item.id" :timestamp="stamp(item)">
        <div class="event">
          <div class="title-row">
            <strong>{{ item.title }}</strong>
            <el-tag size="small" :type="item.category === 'econ' ? 'warning' : 'info'">
              {{ item.category === 'econ' ? '财经' : '科技' }}
            </el-tag>
            <el-tag
              v-if="item.category !== 'econ'"
              size="small"
              :type="item.status === 'confirmed' ? 'success' : 'info'"
            >
              {{ statusLabel[item.status] || item.status }}
            </el-tag>
          </div>
          <p>{{ item.location }} · {{ item.timezone }}</p>
          <p v-if="item.note">{{ item.note }}</p>
          <a v-if="item.url" :href="item.url" target="_blank" rel="noreferrer">官网</a>
        </div>
      </el-timeline-item>
    </el-timeline>
  </section>
</template>

<style scoped>
.page {
  max-width: 880px;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
}

h1 {
  margin: 0 0 8px;
  font-size: 24px;
}

p {
  margin: 0;
  color: var(--muted);
}

.filters {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.timeline {
  margin-top: 4px;
}

.event {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px 14px;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.event p {
  margin-top: 6px;
}
</style>
