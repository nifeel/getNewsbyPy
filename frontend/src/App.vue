<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const active = computed(() => String(route.path))
</script>

<template>
  <el-container class="layout">
    <el-aside width="220px" class="aside">
      <div class="brand">
        <strong>Tech Pulse</strong>
        <span>科技新闻与大事件</span>
      </div>
      <el-menu :default-active="active" router>
        <el-menu-item index="/timeline">时间线</el-menu-item>
        <el-menu-item index="/digest">简报</el-menu-item>
        <el-menu-item index="/calendar">大事件</el-menu-item>
        <el-menu-item index="/sources">来源</el-menu-item>
        <el-menu-item index="/settings">系统设置</el-menu-item>
      </el-menu>
    </el-aside>
    <el-main class="main">
      <router-view />
    </el-main>
  </el-container>
</template>

<style scoped>
.layout {
  min-height: 100vh;
}

.aside {
  background: var(--panel);
  border-right: 1px solid var(--line);
}

.brand {
  padding: 24px 20px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.brand strong {
  font-size: 18px;
}

.brand span {
  color: var(--muted);
  font-size: 12px;
}

.main {
  padding: 24px 28px;
}
</style>
