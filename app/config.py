from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FJ_", env_file=".env", extra="ignore")

    login_url: str = "https://www.financialjuice.com/home"
    target_url: str = "https://www.financialjuice.com/home"
    storage_state_path: Path = Field(default=Path("data/storage_state.json"))
    database_path: Path = Field(default=Path("data/news.db"))
    network_log_path: Path = Field(default=Path("data/logs/network_sniffer.log"))
    startup_api_cache_path: Path = Field(default=Path("data/startup_api_url.txt"))
    user_data_dir: Path = Field(default=Path("data/browser_profile"))
    browser_channel: str | None = Field(default="chrome")
    headless: bool = False
    account: str | None = None
    password: str | None = Field(default=None, repr=False)
    login_retry_attempts: int = 10
    login_retry_interval_seconds: int = 30
    login_success_timeout_seconds: int = 30
    response_preview_chars: int = 1000
    collector_interval_seconds: int = 30
    collector_retry_seconds: int = 10
    history_sync_interval_seconds: int = 60
    login_check_interval_seconds: int = 60
    startup_api_url: str | None = None
    prefer_direct_startup: bool = True
    browser_fallback_enabled: bool = True
    browser_collect_enabled: bool = False
    gui_host: str = "127.0.0.1"
    gui_port: int = 8000

    @property
    def storage_state_file(self) -> Path:
        return self.storage_state_path

    @property
    def storage_state_dir(self) -> Path:
        return self.storage_state_path.parent

    @property
    def database_file(self) -> Path:
        return self.database_path

    @property
    def network_log_file(self) -> Path:
        return self.network_log_path

    @property
    def startup_api_cache_file(self) -> Path:
        return self.startup_api_cache_path

    @property
    def browser_profile_dir(self) -> Path:
        return self.user_data_dir


@lru_cache
def get_settings() -> Settings:
    return Settings()
