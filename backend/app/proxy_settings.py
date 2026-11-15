from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import quote, urlparse

from sqlalchemy.orm import Session

from app.config import settings
from app.models import AppSetting
from app.timeutil import utc_now

PROXY_KEY = "proxy"
PROBE_URL = "https://www.gstatic.com/generate_204"

_cached_url: str | None = None


@dataclass
class ProxyConfig:
    enabled: bool = True
    scheme: str = "http"
    host: str = "127.0.0.1"
    port: int = 7897
    username: str = ""
    password: str = ""

    def to_public_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "scheme": self.scheme,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "has_password": bool(self.password),
        }


def parse_proxy_url(url: str) -> ProxyConfig:
    text = (url or "").strip()
    if not text:
        return ProxyConfig(enabled=False)
    parsed = urlparse(text)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 7897
    scheme = parsed.scheme or "http"
    if scheme not in {"http", "https"}:
        scheme = "http"
    return ProxyConfig(
        enabled=True,
        scheme=scheme,
        host=host,
        port=port,
        username=parsed.username or "",
        password=parsed.password or "",
    )


def build_proxy_url(cfg: ProxyConfig) -> str:
    if not cfg.enabled:
        return ""
    host = cfg.host.strip()
    if not host:
        return ""
    scheme = cfg.scheme if cfg.scheme in {"http", "https"} else "http"
    auth = ""
    if cfg.username.strip():
        auth = f"{quote(cfg.username, safe='')}:{quote(cfg.password, safe='')}@"
    return f"{scheme}://{auth}{host}:{int(cfg.port)}"


def default_proxy_config() -> ProxyConfig:
    return parse_proxy_url(settings.http_proxy)


def _from_json(raw: str) -> ProxyConfig:
    data = json.loads(raw)
    return ProxyConfig(
        enabled=bool(data.get("enabled", True)),
        scheme=str(data.get("scheme") or "http"),
        host=str(data.get("host") or "127.0.0.1"),
        port=int(data.get("port") or 7897),
        username=str(data.get("username") or ""),
        password=str(data.get("password") or ""),
    )


def load_proxy_config(session: Session) -> ProxyConfig:
    row = session.get(AppSetting, PROXY_KEY)
    if row is None:
        cfg = default_proxy_config()
        save_proxy_config(session, cfg)
        return cfg
    return _from_json(row.value)


def save_proxy_config(session: Session, cfg: ProxyConfig) -> ProxyConfig:
    if cfg.enabled:
        if not cfg.host.strip():
            raise ValueError("代理已启用时必须填写主机")
        if not (1 <= int(cfg.port) <= 65535):
            raise ValueError("端口必须在 1-65535")
        if cfg.scheme not in {"http", "https"}:
            raise ValueError("协议只支持 http 或 https")
    row = session.get(AppSetting, PROXY_KEY)
    payload = json.dumps(
        {
            "enabled": cfg.enabled,
            "scheme": cfg.scheme,
            "host": cfg.host.strip(),
            "port": int(cfg.port),
            "username": cfg.username,
            "password": cfg.password,
        },
        ensure_ascii=False,
    )
    now = utc_now()
    if row is None:
        session.add(AppSetting(key=PROXY_KEY, value=payload, updated_at=now))
    else:
        row.value = payload
        row.updated_at = now
    session.commit()
    set_cached_proxy_url(build_proxy_url(cfg))
    return cfg


def set_cached_proxy_url(url: str) -> None:
    global _cached_url
    _cached_url = url


def refresh_proxy_cache(session: Session) -> str:
    url = build_proxy_url(load_proxy_config(session))
    set_cached_proxy_url(url)
    return url


def get_effective_proxy() -> str:
    if _cached_url is not None:
        return _cached_url
    return settings.http_proxy.strip()
