<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  generateSummary,
  getSettings,
  saveFjDatabase,
  saveLlmSettings,
  saveProxySettings,
  saveTranslateSettings,
  testFjDatabase,
  testLlmSettings,
  testProxySettings,
  testTranslateSettings,
} from '../api/client'
import type { FjDatabasePayload, LlmPayload, ProxyPayload, TranslatePayload } from '../types/api'

const loading = ref(false)
const error = ref('')
const fjTesting = ref(false)
const fjSaving = ref(false)
const proxyTesting = ref(false)
const proxySaving = ref(false)
const fjHasPassword = ref(false)
const proxyHasPassword = ref(false)
const fjHint = ref('')
const proxyHint = ref('')
const translateTesting = ref(false)
const translateSaving = ref(false)
const translateHint = ref('')
const llmTesting = ref(false)
const llmSaving = ref(false)
const llmGenerating = ref(false)
const llmHasKey = ref(false)
const llmHint = ref('')

const fj = reactive({
  enabled: true,
  host: '',
  port: 5432,
  database: '',
  username: '',
  password: '',
})

const proxy = reactive({
  enabled: true,
  scheme: 'http',
  host: '127.0.0.1',
  port: 7897,
  username: '',
  password: '',
})

const translate = reactive({
  enabled: true,
  scheme: 'http',
  host: '10.0.0.202',
  port: 8765,
})

const llm = reactive({
  enabled: false,
  base_url: 'http://127.0.0.1:8317/v1',
  api_key: '',
  model: 'claude-3-7-sonnet',
  auto_generate: true,
  auto_generate_hour: 0,
  auto_generate_minute: 5,
})

function fjPayload(): FjDatabasePayload {
  return {
    enabled: fj.enabled,
    host: fj.host.trim(),
    port: Number(fj.port),
    database: fj.database.trim(),
    username: fj.username.trim(),
    ...(fj.password ? { password: fj.password } : {}),
  }
}

function proxyPayload(): ProxyPayload {
  return {
    enabled: proxy.enabled,
    scheme: proxy.scheme,
    host: proxy.host.trim(),
    port: Number(proxy.port),
    username: proxy.username.trim(),
    ...(proxy.password ? { password: proxy.password } : {}),
  }
}

function translatePayload(): TranslatePayload {
  return {
    enabled: translate.enabled,
    scheme: translate.scheme,
    host: translate.host.trim(),
    port: Number(translate.port),
  }
}

function llmPayload(): LlmPayload {
  return {
    enabled: llm.enabled,
    base_url: llm.base_url.trim(),
    model: llm.model.trim(),
    auto_generate: llm.auto_generate,
    auto_generate_hour: Number(llm.auto_generate_hour),
    auto_generate_minute: Number(llm.auto_generate_minute),
    ...(llm.api_key ? { api_key: llm.api_key.trim() } : {}),
  }
}

async function loadSettings() {
  loading.value = true
  error.value = ''
  try {
    const data = await getSettings()
    fj.enabled = data.fj_database.enabled
    fj.host = data.fj_database.host
    fj.port = data.fj_database.port
    fj.database = data.fj_database.database
    fj.username = data.fj_database.username
    fj.password = ''
    fjHasPassword.value = data.fj_database.has_password
    proxy.enabled = data.proxy.enabled
    proxy.scheme = data.proxy.scheme
    proxy.host = data.proxy.host
    proxy.port = data.proxy.port
    proxy.username = data.proxy.username
    proxy.password = ''
    proxyHasPassword.value = data.proxy.has_password
    translate.enabled = data.translate.enabled
    translate.scheme = data.translate.scheme
    translate.host = data.translate.host
    translate.port = data.translate.port
    if (data.llm) {
      llm.enabled = data.llm.enabled
      llm.base_url = data.llm.base_url
      llm.model = data.llm.model
      llm.auto_generate = data.llm.auto_generate
      llm.auto_generate_hour = data.llm.auto_generate_hour
      llm.auto_generate_minute = data.llm.auto_generate_minute
      llm.api_key = ''
      llmHasKey.value = data.llm.has_api_key
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function runFjTest() {
  fjTesting.value = true
  fjHint.value = ''
  try {
    const result = await testFjDatabase(fjPayload())
    fjHint.value = result.message
    if (result.ok) ElMessage.success(result.message)
    else ElMessage.warning(result.message)
  } catch (err) {
    fjHint.value = err instanceof Error ? err.message : '测试失败'
    ElMessage.error(fjHint.value)
  } finally {
    fjTesting.value = false
  }
}

async function saveFj() {
  fjSaving.value = true
  try {
    const data = await saveFjDatabase(fjPayload())
    fj.password = ''
    fjHasPassword.value = data.fj_database.has_password
    ElMessage.success('已保存，采集将按只读方式连接该库')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    fjSaving.value = false
  }
}

async function runProxyTest() {
  proxyTesting.value = true
  proxyHint.value = ''
  try {
    const result = await testProxySettings(proxyPayload())
    proxyHint.value = result.message
    if (result.ok) ElMessage.success(result.message)
    else ElMessage.warning(result.message)
  } catch (err) {
    proxyHint.value = err instanceof Error ? err.message : '测试失败'
    ElMessage.error(proxyHint.value)
  } finally {
    proxyTesting.value = false
  }
}

async function saveProxy() {
  proxySaving.value = true
  try {
    const data = await saveProxySettings(proxyPayload())
    proxy.password = ''
    proxyHasPassword.value = data.proxy.has_password
    ElMessage.success('已保存，采集将立即使用新代理')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    proxySaving.value = false
  }
}

async function runTranslateTest() {
  translateTesting.value = true
  translateHint.value = ''
  try {
    const result = await testTranslateSettings(translatePayload())
    translateHint.value = result.message
    if (result.ok) ElMessage.success(result.message)
    else ElMessage.warning(result.message)
  } catch (err) {
    translateHint.value = err instanceof Error ? err.message : '测试失败'
    ElMessage.error(translateHint.value)
  } finally {
    translateTesting.value = false
  }
}

async function saveTranslate() {
  translateSaving.value = true
  try {
    await saveTranslateSettings(translatePayload())
    ElMessage.success('已保存，英文标题将立即使用该翻译服务')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    translateSaving.value = false
  }
}

async function runLlmTest() {
  llmTesting.value = true
  llmHint.value = ''
  try {
    const result = await testLlmSettings(llmPayload())
    llmHint.value = result.reply ? `${result.message}（回复：${result.reply}）` : result.message
    if (result.ok) ElMessage.success(result.message)
    else ElMessage.warning(result.message)
  } catch (err) {
    llmHint.value = err instanceof Error ? err.message : '测试失败'
    ElMessage.error(llmHint.value)
  } finally {
    llmTesting.value = false
  }
}

async function saveLlm() {
  llmSaving.value = true
  try {
    const data = await saveLlmSettings(llmPayload())
    llm.api_key = ''
    llmHasKey.value = data.llm.has_api_key
    ElMessage.success('已保存，日报生成将使用该 AI 配置')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    llmSaving.value = false
  }
}

async function triggerGenerateYesterday() {
  llmGenerating.value = true
  try {
    const res = await generateSummary(undefined, true)
    ElMessage.success(`生成成功！已生成 ${res.filename}`)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '生成失败')
  } finally {
    llmGenerating.value = false
  }
}

onMounted(loadSettings)
</script>

<template>
  <section class="page">
    <header>
      <h1>系统设置</h1>
      <p>代理、翻译服务、AI 日报网关 (CLIProxyAPI) 与 FinancialJuice 只读库在此配置。密码与密钥不回显，留空表示不修改。采集不会写回 FJ 库。</p>
    </header>

    <el-skeleton v-if="loading" :rows="8" animated />
    <el-result v-else-if="error" icon="error" :title="error">
      <template #extra>
        <el-button @click="loadSettings">重试</el-button>
      </template>
    </el-result>
    <template v-else>
      <h2>FinancialJuice 库</h2>
      <el-form label-position="top" class="panel">
        <el-form-item label="启用">
          <el-switch v-model="fj.enabled" />
        </el-form-item>
        <el-form-item label="主机">
          <el-input v-model="fj.host" placeholder="10.0.0.202" />
        </el-form-item>
        <el-form-item label="端口">
          <el-input-number v-model="fj.port" :min="1" :max="65535" controls-position="right" />
        </el-form-item>
        <el-form-item label="数据库">
          <el-input v-model="fj.database" placeholder="FinancialJuice" />
        </el-form-item>
        <el-form-item label="用户">
          <el-input v-model="fj.username" placeholder="只读账号" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="fj.password"
            type="password"
            show-password
            :placeholder="fjHasPassword ? '已保存，留空不修改' : '输入密码'"
          />
        </el-form-item>
        <el-form-item>
          <el-button :loading="fjTesting" @click="runFjTest">测试连接</el-button>
          <el-button type="primary" :loading="fjSaving" @click="saveFj">保存</el-button>
        </el-form-item>
        <p v-if="fjHint" class="hint">{{ fjHint }}</p>
      </el-form>

      <h2>本机代理</h2>
      <el-form label-position="top" class="panel">
        <el-form-item label="启用">
          <el-switch v-model="proxy.enabled" />
        </el-form-item>
        <el-form-item label="协议">
          <el-select v-model="proxy.scheme" class="full-select">
            <el-option label="http" value="http" />
            <el-option label="https" value="https" />
          </el-select>
        </el-form-item>
        <el-form-item label="主机">
          <el-input v-model="proxy.host" />
        </el-form-item>
        <el-form-item label="端口">
          <el-input-number v-model="proxy.port" :min="1" :max="65535" controls-position="right" />
        </el-form-item>
        <el-form-item label="用户名">
          <el-input v-model="proxy.username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="proxy.password"
            type="password"
            show-password
            :placeholder="proxyHasPassword ? '已保存，留空不修改' : '可选'"
          />
        </el-form-item>
        <el-form-item>
          <el-button :loading="proxyTesting" @click="runProxyTest">测试连接</el-button>
          <el-button type="primary" :loading="proxySaving" @click="saveProxy">保存</el-button>
        </el-form-item>
        <p v-if="proxyHint" class="hint">{{ proxyHint }}</p>
      </el-form>

      <h2>翻译服务</h2>
      <el-form label-position="top" class="panel">
        <el-form-item label="启用">
          <el-switch v-model="translate.enabled" />
        </el-form-item>
        <el-form-item label="协议">
          <el-select v-model="translate.scheme" class="full-select">
            <el-option label="http" value="http" />
            <el-option label="https" value="https" />
          </el-select>
        </el-form-item>
        <el-form-item label="主机">
          <el-input v-model="translate.host" placeholder="10.0.0.202" />
        </el-form-item>
        <el-form-item label="端口">
          <el-input-number v-model="translate.port" :min="1" :max="65535" controls-position="right" />
        </el-form-item>
        <el-form-item>
          <el-button :loading="translateTesting" @click="runTranslateTest">测试连接</el-button>
          <el-button type="primary" :loading="translateSaving" @click="saveTranslate">保存</el-button>
        </el-form-item>
        <p v-if="translateHint" class="hint">{{ translateHint }}</p>
      </el-form>

      <h2>AI 日报生成 (CLIProxyAPI)</h2>
      <el-form label-position="top" class="panel">
        <el-form-item label="启用 AI 服务">
          <el-switch v-model="llm.enabled" />
        </el-form-item>
        <el-form-item label="基础地址 (Base URL)">
          <el-input v-model="llm.base_url" placeholder="http://127.0.0.1:8317/v1" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="llm.api_key"
            type="password"
            show-password
            :placeholder="llmHasKey ? '已保存，留空不修改' : '输入 API Key（若有）'"
          />
        </el-form-item>
        <el-form-item label="模型名称">
          <el-input v-model="llm.model" placeholder="claude-3-7-sonnet" />
        </el-form-item>
        <el-form-item label="每日定时自动生成前日报告">
          <el-switch v-model="llm.auto_generate" />
        </el-form-item>
        <div v-if="llm.auto_generate" class="time-picker-row">
          <el-form-item label="自动触发时间（小时）" style="display: inline-block; margin-right: 16px;">
            <el-input-number v-model="llm.auto_generate_hour" :min="0" :max="23" controls-position="right" />
          </el-form-item>
          <el-form-item label="自动触发时间（分钟）" style="display: inline-block;">
            <el-input-number v-model="llm.auto_generate_minute" :min="0" :max="59" controls-position="right" />
          </el-form-item>
        </div>
        <el-form-item>
          <el-button :loading="llmTesting" @click="runLlmTest">测试连接</el-button>
          <el-button type="primary" :loading="llmSaving" @click="saveLlm">保存</el-button>
          <el-button :loading="llmGenerating" @click="triggerGenerateYesterday">立即生成前日报告</el-button>
        </el-form-item>
        <p v-if="llmHint" class="hint">{{ llmHint }}</p>
      </el-form>
    </template>
  </section>
</template>

<style scoped>
.page {
  max-width: 640px;
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

.panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 16px 16px 4px;
}

.hint {
  margin: 0 0 16px;
  font-size: 13px;
}

.full-select {
  width: 160px;
}

:deep(.el-input-number) {
  width: 160px;
}

@media (max-width: 768px) {
  .full-select,
  :deep(.el-input-number) {
    width: 100%;
  }
}
</style>
