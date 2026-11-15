from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    code: int = 0
    message: str = "ok"
    data: T


class PageData(BaseModel, Generic[T]):
    items: list[T]
    page: int
    page_size: int
    total: int


class ArticleOut(BaseModel):
    id: int
    source_id: str
    source_name: str
    title: str
    url: str
    summary: str
    published_at: datetime | None
    fetched_at: datetime
    lang: str
    kind: str
    level: str = ""
    origin: str = ""


class EventOut(BaseModel):
    id: str
    title: str
    start_date: str
    end_date: str | None
    location: str
    url: str
    status: str
    category: str
    note: str
    timezone: str
    source: str
    time: str = ""


class SourceOut(BaseModel):
    id: str
    name: str
    region: str
    kind: str
    access: str
    enabled: bool
    url: str
    poll_interval_minutes: int
    lang: str
    note: str = ""
    runnable: bool
    last_fetch_at: datetime | None = None
    last_status: str = "idle"
    last_error: str = ""
    last_count: int = 0


class ProxyOut(BaseModel):
    enabled: bool
    scheme: str
    host: str
    port: int
    username: str = ""
    has_password: bool = False


class ProxyIn(BaseModel):
    enabled: bool
    scheme: str = "http"
    host: str = "127.0.0.1"
    port: int = Field(default=7897, ge=1, le=65535)
    username: str = ""
    password: str | None = None


class FjDatabaseOut(BaseModel):
    enabled: bool
    host: str
    port: int
    database: str
    username: str = ""
    has_password: bool = False


class FjDatabaseIn(BaseModel):
    enabled: bool = True
    host: str = ""
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = ""
    username: str = ""
    password: str | None = None


class TranslateOut(BaseModel):
    enabled: bool
    scheme: str
    host: str
    port: int


class TranslateIn(BaseModel):
    enabled: bool = True
    scheme: str = "http"
    host: str = "10.0.0.202"
    port: int = Field(default=8765, ge=1, le=65535)


class LlmOut(BaseModel):
    enabled: bool
    base_url: str
    has_api_key: bool
    api_key_masked: str = ""
    model: str
    auto_generate: bool = True
    auto_generate_hour: int = 0
    auto_generate_minute: int = 5


class LlmIn(BaseModel):
    enabled: bool = True
    base_url: str = "http://127.0.0.1:8317/v1"
    api_key: str | None = None
    model: str = "claude-3-7-sonnet"
    auto_generate: bool = True
    auto_generate_hour: int = Field(default=0, ge=0, le=23)
    auto_generate_minute: int = Field(default=5, ge=0, le=59)


class LlmTestOut(BaseModel):
    ok: bool
    latency_ms: int
    message: str
    model: str = ""
    reply: str = ""


class SettingsOut(BaseModel):
    proxy: ProxyOut
    fj_database: FjDatabaseOut
    translate: TranslateOut
    llm: LlmOut


class FjDatabaseTestOut(BaseModel):
    ok: bool
    latency_ms: int
    message: str
    host: str = ""
    database: str = ""


class ProxyTestOut(BaseModel):
    ok: bool
    latency_ms: int
    message: str
    probe_url: str
    proxy_url: str = ""


class TranslateTestOut(BaseModel):
    ok: bool
    latency_ms: int
    message: str
    url: str = ""
    sample: str = ""


class DigestHeadline(BaseModel):
    time: str = ""
    title: str
    source: str
    url: str = ""
    origin: str = ""
    extra: str = ""


class EconCalendarItem(BaseModel):
    time: str
    biz_date: str
    title: str
    country: str
    impact: str
    forecast: str = ""
    previous: str = ""
    actual: str = ""


class DigestOut(BaseModel):
    kind: str
    date: str
    range_start: str
    range_end: str
    lookahead_start: str
    lookahead_end: str
    timezone: str
    is_sunday: bool
    overview: str
    tech_items: list[DigestHeadline]
    finance_items: list[DigestHeadline]
    hotspot_items: list[DigestHeadline]
    next_events: list[EventOut]
    econ_calendar: list[EconCalendarItem]
    warnings: list[str] = Field(default_factory=list)


class ChartItemOut(BaseModel):
    rank: int
    item_id: str
    title: str
    subtitle: str = ""
    url: str = ""
    category: str = ""
    icon_url: str = ""
    price: str = ""
    stars_today: int | None = None
    stars_total: int | None = None


class ChartOut(BaseModel):
    board: str
    region: str
    chart: str
    snap_date: str | None
    fetched_at: datetime | None
    dates: list[str]
    items: list[ChartItemOut]


class IngestResult(BaseModel):
    fetched_sources: int = 0
    skipped_sources: int = 0
    deferred_sources: int = 0
    inserted: int = 0
    errors: list[str] = Field(default_factory=list)
    running: bool = False


def ok(data: Any) -> dict[str, Any]:
    return {"code": 0, "message": "ok", "data": data}


def fail(message: str, code: int = 1) -> dict[str, Any]:
    return {"code": code, "message": message, "data": None}
