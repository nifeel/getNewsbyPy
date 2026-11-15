from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.config import settings
from app.models import AppSetting
from app.timeutil import utc_now

TRANSLATE_KEY = "translate"
DEFAULT_HOST = "10.0.0.202"
DEFAULT_PORT = 8765

_cached_url: str | None = None


@dataclass
class TranslateConfig:
    enabled: bool = True
    scheme: str = "http"
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    def to_public_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "scheme": self.scheme,
            "host": self.host,
            "port": self.port,
        }


def parse_translate_url(url: str) -> TranslateConfig:
    text = (url or "").strip()
    if not text:
        return TranslateConfig(enabled=False, host="", port=DEFAULT_PORT)
    parsed = urlparse(text)
    scheme = parsed.scheme if parsed.scheme in {"http", "https"} else "http"
    host = parsed.hostname or DEFAULT_HOST
    port = parsed.port or DEFAULT_PORT
    return TranslateConfig(enabled=True, scheme=scheme, host=host, port=port)


def build_translate_url(cfg: TranslateConfig) -> str:
    if not cfg.enabled:
        return ""
    host = cfg.host.strip()
    if not host:
        return ""
    if not (1 <= int(cfg.port) <= 65535):
        return ""
    scheme = cfg.scheme if cfg.scheme in {"http", "https"} else "http"
    return f"{scheme}://{host}:{int(cfg.port)}"


def default_translate_config() -> TranslateConfig:
    return parse_translate_url(settings.translate_url)


def _from_json(raw: str) -> TranslateConfig:
    data = json.loads(raw)
    return TranslateConfig(
        enabled=bool(data.get("enabled", True)),
        scheme=str(data.get("scheme") or "http"),
        host=str(data.get("host") or DEFAULT_HOST),
        port=int(data.get("port") or DEFAULT_PORT),
    )


def load_translate_config(session: Session) -> TranslateConfig:
    row = session.get(AppSetting, TRANSLATE_KEY)
    if row is None:
        cfg = default_translate_config()
        save_translate_config(session, cfg)
        return cfg
    return _from_json(row.value)


def save_translate_config(session: Session, cfg: TranslateConfig) -> TranslateConfig:
    if cfg.enabled:
        if not cfg.host.strip():
            raise ValueError("翻译服务已启用时必须填写主机")
        if not (1 <= int(cfg.port) <= 65535):
            raise ValueError("端口必须在 1-65535")
        if cfg.scheme not in {"http", "https"}:
            raise ValueError("协议只支持 http 或 https")
    payload = json.dumps(
        {
            "enabled": cfg.enabled,
            "scheme": cfg.scheme,
            "host": cfg.host.strip(),
            "port": int(cfg.port),
        },
        ensure_ascii=False,
    )
    now = utc_now()
    row = session.get(AppSetting, TRANSLATE_KEY)
    if row is None:
        session.add(AppSetting(key=TRANSLATE_KEY, value=payload, updated_at=now))
    else:
        row.value = payload
        row.updated_at = now
    session.commit()
    set_cached_translate_url(build_translate_url(cfg))
    return cfg


def set_cached_translate_url(url: str) -> None:
    global _cached_url
    _cached_url = url


def refresh_translate_cache(session: Session) -> str:
    url = build_translate_url(load_translate_config(session))
    set_cached_translate_url(url)
    return url


def get_effective_translate_url() -> str:
    if _cached_url is not None:
        return _cached_url
    return settings.translate_url.strip()
