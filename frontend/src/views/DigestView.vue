<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { getDigest } from '../api/client'
import type { Digest } from '../types/api'

const loading = ref(false)
const error = ref('')
const kind = ref<'daily' | 'weekly'>('daily')
const date = ref('')
const digest = ref<Digest | null>(null)

const subtitle = computed(() => {
  if (!digest.value) return '科技 RSS + FJ 重要/数据；热搜与财经日历现场读取。'
  const item = digest.value
  const span = item.kind === 'weekly' ? `${item.range_start} ~ ${item.range_end}` : item.date
  const ahead = `${item.lookahead_start} ~ ${item.lookahead_end}`
  return `${span}（${item.timezone}）。前瞻事件 ${ahead}。`
})

async function loadDigest() {
  loading.value = true
  error.value = ''
  try {
    digest.value = await getDigest({
      kind: kind.value,
      date: date.value || undefined,
    })
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
    digest.value = null
  } finally {
    loading.value = false
  }
}

onMounted(loadDigest)
</script>

<template>
  <section class="page">
    <header class="header">
      <div>
        <h1>简报</h1>
        <p>{{ subtitle }}</p>
      </div>
    </header>

    <div class="filters">
      <el-radio-group v-model="kind" @change="loadDigest">
        <el-radio-button value="daily">日报</el-radio-button>
        <el-radio-button value="weekly">周报</el-radio-button>
      </el-radio-group>
      <el-date-picker
        v-model="date"
        type="date"
        value-format="YYYY-MM-DD"
        placeholder="业务日，默认今天"
        clearable
        @change="loadDigest"
      />
      <el-button @click="loadDigest">刷新</el-button>
    </div>

    <el-alert
      v-if="digest?.is_sunday && kind === 'weekly'"
      title="今天是周日：周报覆盖本周，下方为下周科技事件与财经日历。"
      type="info"
      :closable="false"
      class="hint"
    />
    <el-alert
      v-for="item in digest?.warnings || []"
      :key="item"
      :title="item"
      type="warning"
      :closable="false"
      class="hint"
    />

    <el-skeleton v-if="loading" :rows="10" animated />
    <el-result v-else-if="error" icon="error" :title="error">
      <template #extra>
        <el-button @click="loadDigest">重试</el-button>
      </template>
    </el-result>
    <template v-else-if="digest">
      <p class="overview">{{ digest.overview }}</p>

      <section class="block">
        <h2>金融要闻</h2>
        <el-empty v-if="digest.finance_items.length === 0" description="该时段无重要/数据稿" />
        <article
          v-for="item in digest.finance_items"
          :key="item.url + item.title"
          class="card"
          :class="{ critical: item.extra === '重要' }"
        >
          <div class="meta">
            <span>{{ item.time }}</span>
            <span>{{ item.source }}</span>
            <span v-if="item.origin">{{ item.origin }}</span>
            <span v-if="item.extra">{{ item.extra }}</span>
          </div>
          <a v-if="item.url" :href="item.url" target="_blank" rel="noreferrer">{{ item.title }}</a>
          <strong v-else>{{ item.title }}</strong>
        </article>
      </section>

      <section class="block">
        <h2>科技</h2>
        <el-empty v-if="digest.tech_items.length === 0" description="该时段暂无科技稿" />
        <article v-for="item in digest.tech_items" :key="item.url" class="card">
          <div class="meta">
            <span>{{ item.time }}</span>
            <span>{{ item.source }}</span>
          </div>
          <a :href="item.url" target="_blank" rel="noreferrer">{{ item.title }}</a>
        </article>
      </section>

      <section class="block">
        <h2>日常热点</h2>
        <el-empty v-if="digest.hotspot_items.length === 0" description="热搜暂不可用" />
        <ol v-else class="hot-list">
          <li v-for="item in digest.hotspot_items" :key="item.title">
            <a :href="item.url" target="_blank" rel="noreferrer">{{ item.title }}</a>
            <span v-if="item.extra" class="tag">{{ item.extra }}</span>
          </li>
        </ol>
      </section>

      <section class="block">
        <h2>{{ kind === 'weekly' ? '下周科技事件' : '未来七日科技事件' }}</h2>
        <el-empty v-if="digest.next_events.length === 0" description="窗口内无事件" />
        <article v-for="item in digest.next_events" :key="item.id" class="card">
          <div class="meta">
            <span>{{ item.start_date }}{{ item.end_date ? ' ~ ' + item.end_date : '' }}</span>
            <span>{{ item.location }}</span>
            <span>{{ item.status === 'confirmed' ? '已确认' : '预计窗口' }}</span>
          </div>
          <a v-if="item.url" :href="item.url" target="_blank" rel="noreferrer">{{ item.title }}</a>
          <strong v-else>{{ item.title }}</strong>
          <p v-if="item.note">{{ item.note }}</p>
        </article>
      </section>

      <section class="block">
        <h2>{{ kind === 'weekly' ? '下周财经日历' : '今日及未来财经日历' }}</h2>
        <el-empty v-if="digest.econ_calendar.length === 0" description="窗口内无高/中影响数据" />
        <el-table v-else :data="digest.econ_calendar" size="small" stripe>
          <el-table-column prop="time" label="时间" width="120" />
          <el-table-column prop="country" label="地区" width="90" />
          <el-table-column prop="impact" label="影响" width="70" />
          <el-table-column prop="title" label="指标" min-width="180" />
          <el-table-column prop="forecast" label="预期" width="90" />
          <el-table-column prop="previous" label="前值" width="90" />
        </el-table>
      </section>
    </template>
  </section>
</template>

<style scoped>
.page { max-width: 880px; }
.header { margin-bottom: 16px; }
h1 { margin: 0 0 8px; font-size: 24px; }
h2 { margin: 0 0 12px; font-size: 16px; }
p { margin: 0; color: var(--muted); }
.filters { display: flex; gap: 12px; align-items: center; margin-bottom: 16px; flex-wrap: wrap; }
.hint { margin-bottom: 10px; }
.overview { margin: 0 0 20px; color: var(--text); line-height: 1.6; }
.block { margin-bottom: 28px; }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; margin-bottom: 8px; }
.card.critical { background: #fde8e8; border-color: #f5c2c0; }
.meta { display: flex; gap: 12px; color: var(--muted); font-size: 12px; margin-bottom: 6px; flex-wrap: wrap; }
.card a, .hot-list a { font-weight: 600; color: var(--text); }
.card p { margin-top: 6px; }
.hot-list { margin: 0; padding-left: 20px; }
.hot-list li { margin-bottom: 8px; }
.tag { margin-left: 8px; color: var(--muted); font-size: 12px; }
</style>
