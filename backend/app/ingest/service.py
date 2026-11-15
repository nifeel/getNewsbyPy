import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.charts_db import charts_configured
from app.fj_settings import load_fj_config, test_fj_connection
from app.ingest.charts import sync_chart_source
from app.ingest.fetchers import get_fetcher
from app.ingest.normalize import canonical_url, sha256_text, title_hash
from app.ingest.remote_events import (
    sync_econ_calendar,
    sync_remote_events,
    sync_weibo_hot,
)
from app.ingest.sources import SourceConfig, load_sources
from app.ingest.types import ProxyRequiredError, RawItem
from app.models import Article, SourceState
from app.news_ws import broadcast_news_updated
from app.schemas import IngestResult
from app.timeutil import utc_now
from app.translate import fill_new_article_titles

logger = logging.getLogger(__name__)
_ingest_lock = asyncio.Lock()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def is_due(
    *,
    poll_interval_minutes: int,
    last_fetch_at: datetime | None,
    now: datetime,
    force: bool,
) -> bool:
    if force or last_fetch_at is None:
        return True
    elapsed = (_as_utc(now) - _as_utc(last_fetch_at)).total_seconds() / 60
    return elapsed >= poll_interval_minutes


def _upsert_state(
    session: Session,
    source_id: str,
    status: str,
    count: int = 0,
    error: str = "",
) -> None:
    state = session.get(SourceState, source_id)
    now = utc_now()
    if state is None:
        state = SourceState(source_id=source_id)
        session.add(state)
    state.last_fetch_at = now
    state.last_status = status
    state.last_count = count
    state.last_error = error[:2000]


def _existing_keys(session: Session, source_id: str, items: list[RawItem]) -> tuple[set[str], set[str]]:
    url_hashes: list[str] = []
    title_hashes: list[str] = []
    for item in items:
        url_hashes.append(sha256_text(canonical_url(item.url)))
        title_hashes.append(title_hash(source_id, item.title))
    if not url_hashes:
        return set(), set()
    rows = session.execute(
        select(Article.url_hash, Article.title_hash).where(
            or_(Article.url_hash.in_(url_hashes), Article.title_hash.in_(title_hashes))
        )
    ).all()
    return {row.url_hash for row in rows}, {row.title_hash for row in rows}


def _skip_reason(source: SourceConfig) -> str:
    if source.kind == "chart" and not charts_configured():
        return "charts database url not configured"
    if source.access == "proxy":
        return "proxy required (intl source)"
    if source.access == "fj_readonly":
        return "fj readonly database url not configured"
    return "disabled or inactive region"


def _probe_fj(session: Session, source: SourceConfig) -> int:
    cfg = load_fj_config(session)
    ok_flag, _latency, message = test_fj_connection(cfg)
    if ok_flag:
        _upsert_state(session, source.id, "ok", count=0, error="直读内网库，不写入本地")
    else:
        _upsert_state(session, source.id, "error", error=message)
    session.commit()
    return 0


async def ingest_source(session: Session, source: SourceConfig) -> int:
    if not source.runnable:
        _upsert_state(session, source.id, "skipped", error=_skip_reason(source))
        session.commit()
        return 0

    if source.access == "fj_readonly":
        return _probe_fj(session, source)

    if source.kind == "event":
        inserted = await sync_remote_events(session, source)
        _upsert_state(session, source.id, "ok", count=inserted)
        session.commit()
        return inserted

    if source.kind == "calendar":
        inserted = await sync_econ_calendar(session)
        _upsert_state(session, source.id, "ok", count=inserted)
        session.commit()
        return inserted

    if source.kind == "hotspot":
        inserted = await sync_weibo_hot(session)
        _upsert_state(session, source.id, "ok", count=inserted)
        session.commit()
        return inserted

    if source.kind == "chart":
        inserted, note = await sync_chart_source(source)
        _upsert_state(session, source.id, "ok", count=inserted, error=note)
        session.commit()
        return inserted

    fetcher = get_fetcher(source)
    items = await fetcher.fetch(source)
    seen_urls, seen_titles = _existing_keys(session, source.id, items)
    inserted = 0
    new_rows: list[Article] = []
    now = utc_now()
    for item in items:
        url = canonical_url(item.url)
        url_key = sha256_text(url)
        title_key = title_hash(source.id, item.title)
        if url_key in seen_urls or title_key in seen_titles:
            continue
        row = Article(
            source_id=source.id,
            title=item.title[:512],
            url=url[:1024],
            summary=item.summary,
            published_at=item.published_at,
            fetched_at=now,
            url_hash=url_key,
            title_hash=title_key,
            lang=source.lang,
            kind=source.kind,
        )
        session.add(row)
        new_rows.append(row)
        seen_urls.add(url_key)
        seen_titles.add(title_key)
        inserted += 1
    _upsert_state(session, source.id, "ok", count=inserted)
    session.commit()
    if new_rows:
        fill_new_article_titles(session, [row.id for row in new_rows])
    return inserted


async def ingest_all(session: Session, *, force: bool = False) -> IngestResult:
    if _ingest_lock.locked():
        return IngestResult(running=True, errors=["ingest already running"])

    result = IngestResult()
    async with _ingest_lock:
        now = utc_now()
        for source in load_sources():
            if not source.runnable:
                result.skipped_sources += 1
                _upsert_state(session, source.id, "skipped", error=_skip_reason(source))
                session.commit()
                continue
            state = session.get(SourceState, source.id)
            if not is_due(
                poll_interval_minutes=source.poll_interval_minutes,
                last_fetch_at=state.last_fetch_at if state else None,
                now=now,
                force=force,
            ):
                result.deferred_sources += 1
                logger.info("defer %s, interval=%sm", source.id, source.poll_interval_minutes)
                continue
            logger.info("fetch sequential %s", source.id)
            try:
                inserted = await ingest_source(session, source)
                result.fetched_sources += 1
                result.inserted += inserted
            except ProxyRequiredError as exc:
                result.skipped_sources += 1
                _upsert_state(session, source.id, "skipped", error=str(exc))
                session.commit()
            except Exception as exc:
                logger.exception("ingest failed for %s", source.id)
                result.errors.append(f"{source.id}: {type(exc).__name__}: {exc}")
                session.rollback()
                _upsert_state(session, source.id, "error", error=str(exc))
                session.commit()
        pending = list(
            session.scalars(
                select(Article.id).where(Article.lang == "en", Article.title_zh == "").limit(40)
            ).all()
        )
        if pending:
            fill_new_article_titles(session, pending)
    if result.inserted > 0:
        await broadcast_news_updated("ingest")
    return result
