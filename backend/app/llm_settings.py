from __future__ import annotations

import json
from dataclasses import dataclass
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AppSetting
from app.timeutil import utc_now

LLM_KEY = "llm"
DEFAULT_BASE_URL = "http://127.0.0.1:8317/v1"
DEFAULT_MODEL = "claude-3-7-sonnet"

_cached_cfg: LlmConfig | None = None


@dataclass
class LlmConfig:
    enabled: bool = False
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    model: str = DEFAULT_MODEL
    auto_generate: bool = True
    auto_generate_hour: int = 0
    auto_generate_minute: int = 5

    def to_public_dict(self) -> dict:
        masked = ""
        if self.api_key:
            if len(self.api_key) <= 8:
                masked = "******"
            else:
                masked = f"{self.api_key[:3]}****{self.api_key[-4:]}"
        return {
            "enabled": self.enabled,
            "base_url": self.base_url,
            "has_api_key": bool(self.api_key),
            "api_key_masked": masked,
            "model": self.model,
            "auto_generate": self.auto_generate,
            "auto_generate_hour": self.auto_generate_hour,
            "auto_generate_minute": self.auto_generate_minute,
        }


def default_llm_config() -> LlmConfig:
    return LlmConfig(
        enabled=bool(getattr(settings, "llm_enabled", False)),
        base_url=str(getattr(settings, "llm_base_url", DEFAULT_BASE_URL) or DEFAULT_BASE_URL).strip(),
        api_key=str(getattr(settings, "llm_api_key", "") or "").strip(),
        model=str(getattr(settings, "llm_model", DEFAULT_MODEL) or DEFAULT_MODEL).strip(),
        auto_generate=bool(getattr(settings, "llm_auto_generate", True)),
        auto_generate_hour=0,
        auto_generate_minute=5,
    )


def _from_json(raw: str) -> LlmConfig:
    data = json.loads(raw)
    return LlmConfig(
        enabled=bool(data.get("enabled", False)),
        base_url=str(data.get("base_url") or DEFAULT_BASE_URL).strip(),
        api_key=str(data.get("api_key") or "").strip(),
        model=str(data.get("model") or DEFAULT_MODEL).strip(),
        auto_generate=bool(data.get("auto_generate", True)),
        auto_generate_hour=int(data.get("auto_generate_hour", 0)),
        auto_generate_minute=int(data.get("auto_generate_minute", 5)),
    )


def load_llm_config(session: Session) -> LlmConfig:
    row = session.get(AppSetting, LLM_KEY)
    if row is None:
        cfg = default_llm_config()
        save_llm_config(session, cfg)
        return cfg
    return _from_json(row.value)


def save_llm_config(session: Session, cfg: LlmConfig) -> LlmConfig:
    if cfg.enabled:
        if not cfg.base_url.strip():
            raise ValueError("AI 服务已启用时必须填写基础地址 (Base URL)")
        if not cfg.model.strip():
            raise ValueError("AI 服务已启用时必须指定模型名称")
        if not (0 <= cfg.auto_generate_hour <= 23):
            raise ValueError("自动生成时间（小时）必须在 0-23 之间")
        if not (0 <= cfg.auto_generate_minute <= 59):
            raise ValueError("自动生成时间（分钟）必须在 0-59 之间")

    payload = json.dumps(
        {
            "enabled": cfg.enabled,
            "base_url": cfg.base_url.strip(),
            "api_key": cfg.api_key.strip(),
            "model": cfg.model.strip(),
            "auto_generate": cfg.auto_generate,
            "auto_generate_hour": cfg.auto_generate_hour,
            "auto_generate_minute": cfg.auto_generate_minute,
        },
        ensure_ascii=False,
    )
    now = utc_now()
    row = session.get(AppSetting, LLM_KEY)
    if row is None:
        session.add(AppSetting(key=LLM_KEY, value=payload, updated_at=now))
    else:
        row.value = payload
        row.updated_at = now
    session.commit()
    set_cached_llm_config(cfg)
    return cfg


def set_cached_llm_config(cfg: LlmConfig) -> None:
    global _cached_cfg
    _cached_cfg = cfg


def refresh_llm_cache(session: Session) -> LlmConfig:
    cfg = load_llm_config(session)
    set_cached_llm_config(cfg)
    return cfg


def get_effective_llm_config() -> LlmConfig:
    if _cached_cfg is not None:
        return _cached_cfg
    return default_llm_config()
