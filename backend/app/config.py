from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TECHPULSE_", extra="ignore")

    database_url: str = f"sqlite:///{BACKEND_DIR / 'data' / 'techpulse.db'}"
    rsshub_base: str = "http://127.0.0.1:1200"
    ingest_interval_minutes: int = 10
    event_sync_hour: int = 7
    # cn / intl / all；intl 源只走本机代理
    active_region: str = "all"
    user_agent: str = "TechPulse/0.1 (+local; news-aggregator)"
    http_timeout_seconds: float = 20.0
    event_http_timeout_seconds: float = 45.0
    # 本机 Clash 混合端口；直连超时后自动走这里重试
    http_proxy: str = "http://127.0.0.1:7897"
    # 内网 FinancialJuice 只读库。必须是 SELECT-only 账号，禁止业主密码，本服务永不写回。
    fj_database_url: str = ""
    fj_sync_batch_size: int = 300
    # 榜单（GitHub 热榜、App Store）写入的 PostgreSQL 库，必须是独立库，不能指向 FinancialJuice。
    # 例 postgresql+psycopg://user:pass@127.0.0.1:5432/techpulse；也可 TECHPULSE_CHARTS_DATABASE_URL
    charts_database_url: str = ""
    fj_title_lookback: int = 3000
    # getNewsbyPy 翻译接口基址；系统设置页可改。也可 TECHPULSE_TRANSLATE_URL
    translate_url: str = "http://10.0.0.202:8765"
    # CLIProxyAPI / LLM 接入配置；系统设置页可改。也可 TECHPULSE_LLM_*
    llm_enabled: bool = False
    llm_base_url: str = "http://127.0.0.1:8317/v1"
    llm_api_key: str = ""
    llm_model: str = "claude-3-7-sonnet"
    llm_auto_generate: bool = True
    # 公网访问共享密钥。空则不校验（仅内网）。也可 TECHPULSE_ACCESS_KEY
    access_key: str = ""


settings = Settings()
