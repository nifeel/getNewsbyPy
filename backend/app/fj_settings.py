from __future__ import annotations

import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AppSetting
from app.timeutil import utc_now

FJ_KEY = "fj_database"
PROBE_SQL = "SELECT newsid FROM news LIMIT 1"

_cached_conninfo: str | None = None


@dataclass
class FjDbConfig:
    enabled: bool = True
    host: str = ""
    port: int = 5432
    database: str = ""
    username: str = ""
    password: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "username": self.username,
            "has_password": bool(self.password),
        }


def build_conninfo(cfg: FjDbConfig) -> str:
    if not cfg.enabled:
        return ""
    host = cfg.host.strip()
    database = cfg.database.strip()
    username = cfg.username.strip()
    if not host or not database or not username:
        return ""
    if not (1 <= int(cfg.port) <= 65535):
        return ""
    return make_conninfo(
        host=host,
        port=int(cfg.port),
        dbname=database,
        user=username,
        password=cfg.password or None,
    )


def default_fj_config() -> FjDbConfig:
    raw = settings.fj_database_url.strip()
    if not raw:
        return FjDbConfig(enabled=False)
    try:
        info = conninfo_to_dict(raw)
    except Exception:  # noqa: BLE001 - 环境变量格式不对就当未配置
        return FjDbConfig(enabled=False)
    port_raw = info.get("port") or 5432
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        port = 5432
    return FjDbConfig(
        enabled=True,
        host=str(info.get("host") or ""),
        port=port,
        database=str(info.get("dbname") or ""),
        username=str(info.get("user") or ""),
        password=str(info.get("password") or ""),
    )


def _from_json(raw: str) -> FjDbConfig:
    data = json.loads(raw)
    return FjDbConfig(
        enabled=bool(data.get("enabled", True)),
        host=str(data.get("host") or ""),
        port=int(data.get("port") or 5432),
        database=str(data.get("database") or ""),
        username=str(data.get("username") or ""),
        password=str(data.get("password") or ""),
    )


def load_fj_config(session: Session) -> FjDbConfig:
    row = session.get(AppSetting, FJ_KEY)
    if row is None:
        cfg = default_fj_config()
        save_fj_config(session, cfg)
        return cfg
    return _from_json(row.value)


def save_fj_config(session: Session, cfg: FjDbConfig) -> FjDbConfig:
    if cfg.enabled:
        if not cfg.host.strip():
            raise ValueError("已启用时必须填写主机")
        if not cfg.database.strip():
            raise ValueError("已启用时必须填写数据库名")
        if not cfg.username.strip():
            raise ValueError("已启用时必须填写用户")
        if not (1 <= int(cfg.port) <= 65535):
            raise ValueError("端口必须在 1-65535")
    payload = json.dumps(
        {
            "enabled": cfg.enabled,
            "host": cfg.host.strip(),
            "port": int(cfg.port),
            "database": cfg.database.strip(),
            "username": cfg.username.strip(),
            "password": cfg.password,
        },
        ensure_ascii=False,
    )
    now = utc_now()
    row = session.get(AppSetting, FJ_KEY)
    if row is None:
        session.add(AppSetting(key=FJ_KEY, value=payload, updated_at=now))
    else:
        row.value = payload
        row.updated_at = now
    session.commit()
    set_cached_fj_conninfo(build_conninfo(cfg))
    return cfg


def set_cached_fj_conninfo(conninfo: str) -> None:
    global _cached_conninfo
    _cached_conninfo = conninfo


def refresh_fj_cache(session: Session) -> str:
    url = build_conninfo(load_fj_config(session))
    set_cached_fj_conninfo(url)
    return url


def get_effective_fj_url() -> str:
    if _cached_conninfo is not None:
        return _cached_conninfo
    return settings.fj_database_url.strip()


def _safe_error(exc: BaseException, password: str) -> str:
    detail = str(exc)
    if password and password in detail:
        return type(exc).__name__
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def test_fj_connection(cfg: FjDbConfig) -> tuple[bool, int, str]:
    conninfo = build_conninfo(cfg)
    if not conninfo:
        return False, 0, "主机、数据库、用户不能为空"
    started = perf_counter()
    try:
        with psycopg.connect(
            conninfo,
            row_factory=dict_row,
            autocommit=True,
            connect_timeout=8,
            options="-c default_transaction_read_only=on",
        ) as conn, conn.cursor() as cur:
            cur.execute(PROBE_SQL)
            cur.fetchone()
        latency = int((perf_counter() - started) * 1000)
        return True, latency, f"只读连接成功，news 可查询，耗时 {latency} ms"
    except Exception as exc:  # noqa: BLE001 - 测试结果回给界面
        latency = int((perf_counter() - started) * 1000)
        return False, latency, _safe_error(exc, cfg.password)
