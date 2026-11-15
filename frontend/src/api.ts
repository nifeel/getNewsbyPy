export interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

export interface HealthInfo {
  status: string
  region: string
  auth_required: boolean
}

export interface PageData<T> {
  items: T[]
  page: number
  page_size: number
  total: number
}

export interface Article {
  id: number
  source_id: string
  source_name: string
  title: string
  url: string
  summary: string
  published_at: string | null
  fetched_at: string
  lang: string
  kind: string
  level: string
  origin: string
}

export interface NewsWsEvent {
  type: 'news_updated'
  data: { reason: 'fj' | 'ingest' }
}

export interface EventItem {
  id: string
  title: string
  start_date: string
  end_date: string | null
  location: string
  url: string
  status: string
  category: string
  note: string
  timezone: string
  source: string
  time: string
}

export interface SourceItem {
  id: string
  name: string
  region: string
  kind: string
  access: string
  enabled: boolean
  url: string
  poll_interval_minutes: number
  lang: string
  note: string
  runnable: boolean
  last_fetch_at: string | null
  last_status: string
  last_error: string
  last_count: number
}

export type ChartBoard = 'github' | 'appstore'

export interface ChartItem {
  rank: number
  item_id: string
  title: string
  subtitle: string
  url: string
  category: string
  icon_url: string
  price: string
  stars_today: number | null
  stars_total: number | null
}

export interface ChartData {
  board: ChartBoard
  region: string
  chart: string
  snap_date: string | null
  fetched_at: string | null
  dates: string[]
  items: ChartItem[]
}

export interface DigestHeadline {
  time: string
  title: string
  source: string
  url: string
  origin: string
  extra: string
}

export interface EconCalendarItem {
  time: string
  biz_date: string
  title: string
  country: string
  impact: string
  forecast: string
  previous: string
  actual: string
}

export interface Digest {
  kind: string
  date: string
  range_start: string
  range_end: string
  lookahead_start: string
  lookahead_end: string
  timezone: string
  is_sunday: boolean
  overview: string
  tech_items: DigestHeadline[]
  finance_items: DigestHeadline[]
  hotspot_items: DigestHeadline[]
  next_events: EventItem[]
  econ_calendar: EconCalendarItem[]
  warnings: string[]
}

export interface IngestResult {
  fetched_sources: number
  skipped_sources: number
  deferred_sources: number
  inserted: number
  errors: string[]
  running: boolean
}

export interface ProxySettings {
  enabled: boolean
  scheme: string
  host: string
  port: number
  username: string
  has_password: boolean
}

export interface FjDatabaseSettings {
  enabled: boolean
  host: string
  port: number
  database: string
  username: string
  has_password: boolean
}

export interface TranslateSettings {
  enabled: boolean
  scheme: string
  host: string
  port: number
}

export interface LlmSettings {
  enabled: boolean
  base_url: string
  has_api_key: boolean
  api_key_masked: string
  model: string
  auto_generate: boolean
  auto_generate_hour: number
  auto_generate_minute: number
}

export interface AppSettings {
  proxy: ProxySettings
  fj_database: FjDatabaseSettings
  translate: TranslateSettings
  llm: LlmSettings
}

export interface ProxyTestResult {
  ok: boolean
  latency_ms: number
  message: string
  probe_url: string
  proxy_url: string
}

export interface FjDatabaseTestResult {
  ok: boolean
  latency_ms: number
  message: string
  host: string
  database: string
}

export interface TranslateTestResult {
  ok: boolean
  latency_ms: number
  message: string
  url: string
  sample: string
}

export interface LlmTestResult {
  ok: boolean
  latency_ms: number
  message: string
  model: string
  reply: string
}

export interface ProxyPayload {
  enabled: boolean
  scheme: string
  host: string
  port: number
  username: string
  password?: string
}

export interface FjDatabasePayload {
  enabled: boolean
  host: string
  port: number
  database: string
  username: string
  password?: string
}

export interface TranslatePayload {
  enabled: boolean
  scheme: string
  host: string
  port: number
}

export interface LlmPayload {
  enabled: boolean
  base_url: string
  api_key?: string
  model: string
  auto_generate: boolean
  auto_generate_hour: number
  auto_generate_minute: number
}

export interface SummaryGenerateResult {
  date: string
  filename: string
}

export interface SummaryItem {
  date: string
  title: string
  filename: string
}

export interface SummaryDetail {
  date: string
  title: string
  markdown: string
}
