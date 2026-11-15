from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.timeutil import BIZ_ZONE

import psycopg
from psycopg.rows import dict_row

from app.fj_settings import get_effective_fj_url
from app.ingest.fetchers.financialjuice import (
    _NEWS_SELECT,
    display_lang,
    display_summary,
    display_title,
    news_url,
    parse_fj_datetime,
    row_from_mapping,
)
from app.ingest.sources import get_source_map
from app.schemas import ArticleOut

logger = logging.getLogger(__name__)

FJ_SOURCE_ID = "financialjuice"


def fj_configured() -> bool:
    return bool(get_effective_fj_url())


def _filters(
    q: str | None,
    lang: str | None,
    level: str | None = None,
    published_from: str | None = None,
    published_to: str | None = None,
) -> tuple[str, list[object]]:
    clauses = ["TRUE"]
    params: list[object] = []
    if q and q.strip():
        like = f"%{q.strip()}%"
        clauses.append(
            "(title ILIKE %s OR COALESCE(title_zh, '') ILIKE %s OR description ILIKE %s)"
        )
        params.extend([like, like, like])
    if lang == "zh":
        clauses.append("title_zh IS NOT NULL AND btrim(title_zh) <> ''")
    elif lang == "en":
        clauses.append("(title_zh IS NULL OR btrim(title_zh) = '')")
    if level == "__empty__":
        clauses.append("(level IS NULL OR btrim(level) = '')")
    elif level:
        clauses.append("btrim(level) = %s")
        params.append(level.strip())
    if published_from:
        clauses.append("datepublished >= %s")
        params.append(published_from)
    if published_to:
        clauses.append("datepublished < %s")
        params.append(published_to)
    return " AND ".join(clauses), params


def _connect():
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


def to_article_out(raw: dict) -> ArticleOut | None:
    row = row_from_mapping(raw)
    if row is None:
        return None
    title = display_title(row)
    if not title:
        return None
    published = parse_fj_datetime(row.date_published)
    source = get_source_map().get(FJ_SOURCE_ID)
    return ArticleOut(
        id=row.news_id,
        source_id=FJ_SOURCE_ID,
        source_name=source.name if source else "FinancialJuice",
        title=title[:512],
        url=news_url(row.news_id, row.eurl)[:1024],
        summary=display_summary(row),
        published_at=published,
        fetched_at=published or datetime(1970, 1, 1, tzinfo=UTC),
        lang=display_lang(row),
        kind=source.kind if source else "news",
        level=row.level.strip(),
        origin=row.fc_name.strip(),
    )


def count_fj_news(
    *,
    q: str | None = None,
    lang: str | None = None,
    level: str | None = None,
    published_from: str | None = None,
    published_to: str | None = None,
) -> int:
    where, params = _filters(q, lang, level, published_from, published_to)
    sql = f"SELECT COUNT(*) AS n FROM news WHERE {where}"
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone() or {}
    return int(row.get("n") or 0)


def peek_max_newsid() -> int:
    if not fj_configured():
        return 0
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(newsid), 0) AS n FROM news")
            row = cur.fetchone() or {}
        return int(row.get("n") or 0)
    except Exception:
        logger.exception("fj peek max newsid failed")
        return 0


def list_fj_news(
    *,
    q: str | None = None,
    lang: str | None = None,
    level: str | None = None,
    published_from: str | None = None,
    published_to: str | None = None,
    limit: int,
    offset: int,
) -> list[ArticleOut]:
    where, params = _filters(q, lang, level, published_from, published_to)
    sql = (
        f"SELECT {_NEWS_SELECT} FROM news WHERE {where} "
        "ORDER BY datepublished DESC NULLS LAST, newsid DESC "
        "LIMIT %s OFFSET %s"
    )
    params = [*params, max(0, int(limit)), max(0, int(offset))]
    items: list[ArticleOut] = []
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        for raw in cur.fetchall():
            item = to_article_out(raw)
            if item is not None:
                items.append(item)
    return items


def item_sort_ts(item: ArticleOut) -> datetime:
    value = item.published_at or item.fetched_at
    if value.tzinfo is None:
        value = value.replace(tzinfo=BIZ_ZONE)
    return value.astimezone(UTC)


def merge_news_page(
    left: list[ArticleOut],
    right: list[ArticleOut],
    *,
    offset: int,
    limit: int,
) -> list[ArticleOut]:
    merged = sorted([*left, *right], key=item_sort_ts, reverse=True)
    start = max(0, offset)
    return merged[start : start + limit]
