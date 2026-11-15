from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.fj_settings import get_effective_fj_url
from app.ingest.fetchers.base import BaseFetcher
from app.ingest.sources import SourceConfig
from app.ingest.types import RawItem
from app.models import AppSetting
from app.timeutil import utc_now

logger = logging.getLogger(__name__)

CURSOR_KEY = "fj_news_cursor"
# PostgreSQL 未加引号的列名会折成小写，与 getNewsbyPy SCHEMA_PG 一致
_NEWS_SELECT = (
    "newsid, title, title_zh, datepublished, description, fcname, level, eurl"
)
_NEWS_PATH_ID = re.compile(r"/News/(\d+)(?:/|\.aspx|\?|$)", re.IGNORECASE)
_NEWS_QUERY_ID = re.compile(r"(?:^|[?&])newsId=(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class FjNewsRow:
    news_id: int
    title: str
    title_zh: str | None
    date_published: Any
    description: str
    fc_name: str
    level: str
    eurl: str


def news_url(news_id: int, eurl: str | None = None) -> str:
    text = (eurl or "").strip()
    if text.startswith(("http://", "https://")):
        return text
    return f"https://www.financialjuice.com/News/{int(news_id)}"


def parse_news_id(url: str) -> int | None:
    path_match = _NEWS_PATH_ID.search(url)
    if path_match:
        return int(path_match.group(1))
    query_match = _NEWS_QUERY_ID.search(url)
    if query_match:
        return int(query_match.group(1))
    return None


def load_cursor(session: Session | None = None) -> int:
    own = session is None
    db = session or SessionLocal()
    try:
        row = db.get(AppSetting, CURSOR_KEY)
        if row is None:
            return 0
        data = json.loads(row.value)
        return int(data.get("last_newsid") or 0)
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0
    finally:
        if own:
            db.close()


def save_cursor(last_newsid: int, session: Session) -> None:
    payload = json.dumps({"last_newsid": int(last_newsid)}, ensure_ascii=False)
    row = session.get(AppSetting, CURSOR_KEY)
    now = utc_now()
    if row is None:
        session.add(AppSetting(key=CURSOR_KEY, value=payload, updated_at=now))
    else:
        row.value = payload
        row.updated_at = now


def advance_cursor(items: list[RawItem], previous: int, session: Session) -> int:
    ids = [parse_news_id(item.url) or 0 for item in items]
    nxt = max([previous, *ids], default=previous)
    if nxt > previous:
        save_cursor(nxt, session)
    return nxt


def _row_get(row: Mapping[str, Any], *names: str) -> Any:
    lower = {str(key).lower(): value for key, value in row.items()}
    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]
    return None


def parse_fj_datetime(value: object) -> datetime | None:
    """FJ DatePublished 是无时区的 UTC 墙钟（与 fetched_at 的上海时间差 8 小时）。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def display_title(row: FjNewsRow) -> str:
    zh = (row.title_zh or "").strip()
    en = (row.title or "").strip()
    return zh or en


def display_lang(row: FjNewsRow) -> str:
    return "zh" if (row.title_zh or "").strip() else "en"


def display_summary(row: FjNewsRow) -> str:
    return (row.description or "").strip()


def row_from_mapping(raw: Mapping[str, Any]) -> FjNewsRow | None:
    news_id = _row_get(raw, "NewsID", "newsid")
    try:
        parsed_id = int(news_id)
    except (TypeError, ValueError):
        return None
    if parsed_id <= 0:
        return None
    title_zh = _row_get(raw, "title_zh")
    zh_text = str(title_zh).strip() if title_zh is not None else ""
    return FjNewsRow(
        news_id=parsed_id,
        title=str(_row_get(raw, "Title", "title") or ""),
        title_zh=zh_text or None,
        date_published=_row_get(raw, "DatePublished", "datepublished"),
        description=str(_row_get(raw, "Description", "description") or ""),
        fc_name=str(_row_get(raw, "FCName", "fcname") or ""),
        level=str(_row_get(raw, "Level", "level") or ""),
        eurl=str(_row_get(raw, "eurl", "EUrl") or "").strip(),
    )


def to_raw_item(row: FjNewsRow) -> RawItem | None:
    title = display_title(row)
    if not title:
        return None
    return RawItem(
        title=title,
        url=news_url(row.news_id, row.eurl),
        summary=display_summary(row),
        published_at=parse_fj_datetime(row.date_published),
        lang=display_lang(row),
        replace_existing=True,
    )


def _connect_readonly():
    url = get_effective_fj_url()
    if not url:
        raise RuntimeError("未配置 FinancialJuice 只读库")
    return psycopg.connect(
        url,
        row_factory=dict_row,
        autocommit=True,
        connect_timeout=10,
        options="-c default_transaction_read_only=on",
    )


def _fetch_rows(since_id: int) -> list[FjNewsRow]:
    batch = max(1, int(settings.fj_sync_batch_size))
    lookback = max(0, int(settings.fj_title_lookback))
    sql_new = (
        f"SELECT {_NEWS_SELECT} FROM news "
        "WHERE newsid > %s ORDER BY newsid ASC LIMIT %s"
    )
    sql_zh = (
        f"SELECT {_NEWS_SELECT} FROM news "
        "WHERE newsid > %s AND newsid <= %s "
        "AND title_zh IS NOT NULL AND btrim(title_zh) <> '' "
        "ORDER BY newsid DESC LIMIT %s"
    )
    by_id: dict[int, FjNewsRow] = {}
    with _connect_readonly() as conn, conn.cursor() as cur:
        cur.execute(sql_new, (since_id, batch))
        for raw in cur.fetchall():
            mapped = row_from_mapping(raw)
            if mapped:
                by_id[mapped.news_id] = mapped
        if since_id > 0 and lookback > 0:
            low = max(0, since_id - lookback)
            cur.execute(sql_zh, (low, since_id, batch))
            for raw in cur.fetchall():
                mapped = row_from_mapping(raw)
                if mapped:
                    by_id.setdefault(mapped.news_id, mapped)
    return [by_id[key] for key in sorted(by_id)]


class FinancialJuiceFetcher(BaseFetcher):
    """只读内网 FinancialJuice 库，禁止写回。"""

    access = "fj_readonly"

    async def fetch(self, source: SourceConfig) -> list[RawItem]:
        since_id = load_cursor()
        rows = _fetch_rows(since_id)
        items: list[RawItem] = []
        for row in rows:
            item = to_raw_item(row)
            if item is not None:
                items.append(item)
        logger.info(
            "fj readonly fetched %s rows since %s source=%s",
            len(items),
            since_id,
            source.id,
        )
        return items
