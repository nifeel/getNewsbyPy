import axios from 'axios'
import type {
  ApiResponse,
  AppSettings,
  Article,
  ChartBoard,
  ChartData,
  Digest,
  EventItem,
  FjDatabasePayload,
  FjDatabaseTestResult,
  HealthInfo,
  IngestResult,
  LlmPayload,
  LlmTestResult,
  PageData,
  ProxyPayload,
  ProxyTestResult,
  SourceItem,
  SummaryDetail,
  SummaryGenerateResult,
  SummaryItem,
  TranslatePayload,
  TranslateTestResult,
} from '../types/api'
import { clearAccessKey, getAccessKey } from '../utils/access'

const http = axios.create({
  baseURL: '/',
  timeout: 30000,
})

http.interceptors.request.use((config) => {
  const key = getAccessKey()
  if (key) {
    config.headers['X-Access-Key'] = key
  }
  return config
})

http.interceptors.response.use(
  (response) => {
    const payload = response.data as ApiResponse<unknown>
    if (payload.code !== 0) {
      return Promise.reject(new Error(payload.message || '请求失败'))
    }
    return response
  },
  (error: unknown) => {
    const status = axios.isAxiosError(error) ? error.response?.status : undefined
    if (status === 401 && !location.pathname.startsWith('/unlock')) {
      clearAccessKey()
      location.assign('/unlock')
    }
    const message = axios.isAxiosError(error)
      ? (error.response?.data as ApiResponse<unknown> | undefined)?.message || error.message
      : '请求失败'
    return Promise.reject(new Error(message))
  },
)

export async function getHealth(): Promise<HealthInfo> {
  const { data } = await http.get<ApiResponse<HealthInfo>>('/api/health')
  return data.data
}

export async function checkAccessKey(key: string): Promise<void> {
  await http.post<ApiResponse<{ ok: boolean }>>('/api/access/check', { key })
}

export async function getNews(params: {
  page: number
  page_size: number
  source_id?: string
  q?: string
  lang?: string
  level?: string
}): Promise<PageData<Article>> {
  const { data } = await http.get<ApiResponse<PageData<Article>>>('/api/news', { params })
  return data.data
}

export async function getEvents(): Promise<EventItem[]> {
  const { data } = await http.get<ApiResponse<EventItem[]>>('/api/events', { timeout: 60000 })
  return data.data
}

export async function getDigest(params: { kind: 'daily' | 'weekly'; date?: string }): Promise<Digest> {
  const { data } = await http.get<ApiResponse<Digest>>('/api/digest', {
    params,
    timeout: 60000,
  })
  return data.data
}

export async function getSummaries(): Promise<SummaryItem[]> {
  const { data } = await http.get<ApiResponse<{ items: SummaryItem[] }>>('/api/summaries')
  return data.data.items
}

export async function getSummary(day: string): Promise<SummaryDetail> {
  const { data } = await http.get<ApiResponse<SummaryDetail>>(`/api/summaries/${day}`)
  return data.data
}

export async function getChart(params: {
  board: ChartBoard
  region?: string
  chart?: string
  date?: string
}): Promise<ChartData> {
  const { data } = await http.get<ApiResponse<ChartData>>('/api/charts', { params })
  return data.data
}

export async function getSources(): Promise<SourceItem[]> {
  const { data } = await http.get<ApiResponse<SourceItem[]>>('/api/sources')
  return data.data
}

export async function triggerIngest(): Promise<IngestResult> {
  const { data } = await http.post<ApiResponse<IngestResult>>('/api/ingest', null, { timeout: 180000 })
  return data.data
}

export async function syncEvents(): Promise<{ upserted: number }> {
  const { data } = await http.post<ApiResponse<{ upserted: number }>>('/api/events/sync')
  return data.data
}

export async function getSettings(): Promise<AppSettings> {
  const { data } = await http.get<ApiResponse<AppSettings>>('/api/settings')
  return data.data
}

export async function saveProxySettings(payload: ProxyPayload): Promise<AppSettings> {
  const { data } = await http.put<ApiResponse<AppSettings>>('/api/settings', payload)
  return data.data
}

export async function testProxySettings(payload: ProxyPayload): Promise<ProxyTestResult> {
  const { data } = await http.post<ApiResponse<ProxyTestResult>>('/api/settings/proxy/test', payload, {
    timeout: 20000,
  })
  return data.data
}

export async function saveFjDatabase(payload: FjDatabasePayload): Promise<AppSettings> {
  const { data } = await http.put<ApiResponse<AppSettings>>('/api/settings/fj-database', payload)
  return data.data
}

export async function testFjDatabase(payload: FjDatabasePayload): Promise<FjDatabaseTestResult> {
  const { data } = await http.post<ApiResponse<FjDatabaseTestResult>>(
    '/api/settings/fj-database/test',
    payload,
    { timeout: 20000 },
  )
  return data.data
}

export async function saveTranslateSettings(payload: TranslatePayload): Promise<AppSettings> {
  const { data } = await http.put<ApiResponse<AppSettings>>('/api/settings/translate', payload)
  return data.data
}

export async function testTranslateSettings(payload: TranslatePayload): Promise<TranslateTestResult> {
  const { data } = await http.post<ApiResponse<TranslateTestResult>>(
    '/api/settings/translate/test',
    payload,
    { timeout: 20000 },
  )
  return data.data
}

export async function saveLlmSettings(payload: LlmPayload): Promise<AppSettings> {
  const { data } = await http.put<ApiResponse<AppSettings>>('/api/settings/llm', payload)
  return data.data
}

export async function testLlmSettings(payload: LlmPayload): Promise<LlmTestResult> {
  const { data } = await http.post<ApiResponse<LlmTestResult>>('/api/settings/llm/test', payload, {
    timeout: 25000,
  })
  return data.data
}

export async function generateSummary(
  day?: string,
  overwrite: boolean = false,
): Promise<SummaryGenerateResult> {
  const { data } = await http.post<ApiResponse<SummaryGenerateResult>>(
    '/api/summaries/generate',
    null,
    {
      params: {
        ...(day ? { day } : {}),
        overwrite,
      },
      timeout: 150000,
    },
  )
  return data.data
}
