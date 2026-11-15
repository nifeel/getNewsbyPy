import json
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from app.storage.task_status import cst_now

AUTH_COOKIE_NAMES = (".ASPXAUTH", "FJ-UID", "FJ-UName")


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def storage_state_exists(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0


def earliest_auth_cookie_expiry(path: Path) -> datetime | None:
    """认证 cookie 中最早过期时间（UTC aware）。没有可解析过期则返回 None。"""
    if not storage_state_exists(path):
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    cookies = payload.get("cookies")
    if not isinstance(cookies, list):
        return None
    expiries: list[datetime] = []
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        name = str(cookie.get("name") or "")
        if name not in AUTH_COOKIE_NAMES:
            continue
        raw_expires = cookie.get("expires")
        try:
            ts = float(raw_expires)
        except (TypeError, ValueError):
            continue
        if ts <= 0:
            continue
        expiries.append(datetime.fromtimestamp(ts, tz=timezone.utc))
    if not expiries:
        return None
    return min(expiries)


def format_cookie_expires_at(expires: datetime | None) -> str:
    if expires is None:
        return ""
    return expires.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def persist_cookie_expiry(path: Path) -> None:
    """把认证 cookie 过期时间写入 gui_settings.cookie_expires_at（UTC）。"""
    from app.config import get_settings
    from app.storage.database import connect, init_db

    value = format_cookie_expires_at(earliest_auth_cookie_expiry(path))
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        conn.execute(
            """
            INSERT INTO gui_settings (key, value, updated_at)
            VALUES ('cookie_expires_at', ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (value, cst_now()),
        )
        conn.commit()
