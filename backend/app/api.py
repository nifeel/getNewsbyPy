import logging
from datetime import UTC, date

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.charts_db import charts_configured, get_charts_engine, list_snap_dates, read_snapshot
from app.config import settings
from app.db import get_session
from app.fj_read import (
    FJ_SOURCE_ID,
    count_fj_news,
    fj_configured,
    list_fj_news,
    merge_news_page,
)
from app.digest import build_digest
from app.ingest.events import list_events, sync_cn_events, to_event_out
from app.ingest.fetchers.charts import APPSTORE_CHARTS, APPSTORE_REGIONS
from app.ingest.remote_events import ensure_econ_events
from app.ingest.service import ingest_all
from app.ingest.sources import get_source_map, load_sources
from app.models import Article, SourceState
from app.schemas import (
    ArticleOut,
    ChartItemOut,
    ChartOut,
    IngestResult,
    PageData,
    SourceOut,
    fail,
    ok,
)
from app.summaries import list_summaries, read_summary
from app.timeutil import BIZ_ZONE, assume_tz
from app.translate import apply_event_title_zh, apply_title_zh

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def _source_name(source_id: str) -> str:
    item = get_source_map().get(source_id)
    return item.name if item else source_id


@router.get("/health")
def health(request: Request) -> dict:
    from app.access import public_auth_required

    return ok(
        {
            "status": "ok",
            "region": settings.active_region,
            "auth_required": public_auth_required(request),
        }
    )


class AccessCheckIn(BaseModel):
    key: str = ""


@router.post("/access/check")
def access_check(body: AccessCheckIn) -> dict:
    from app.access import access_key_ok

    if access_key_ok(body.key):
        return ok({"ok": True})
    return fail("密钥不正确")


def _article_out(row: Article) -> ArticleOut:
    title = (row.title_zh or "").strip() or row.title
    return ArticleOut(
        id=row.id,
        source_id=row.source_id,
        source_name=_source_name(row.source_id),
        title=title,
        url=row.url,
        summary=row.summary,
        published_at=assume_tz(row.published_at, BIZ_ZONE) if row.published_at else None,
        fetched_at=assume_tz(row.fetched_at, UTC),
        lang=row.lang,
        kind=row.kind,
    )


def _local_news_query(
    session: Session,
    *,
    source_id: str | None,
    q: str | None,
    lang: str | None,
):
    stmt = select(Article).where(Article.source_id != FJ_SOURCE_ID)
    count_stmt = select(func.count(Article.id)).where(Article.source_id != FJ_SOURCE_ID)
    if source_id:
        stmt = stmt.where(Article.source_id == source_id)
        count_stmt = count_stmt.where(Article.source_id == source_id)
    if lang:
        stmt = stmt.where(Article.lang == lang)
        count_stmt = count_stmt.where(Article.lang == lang)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Article.title.like(like), Article.title_zh.like(like)))
        count_stmt = count_stmt.where(or_(Article.title.like(like), Article.title_zh.like(like)))
    stmt = stmt.order_by(Article.published_at.desc().nullslast(), Article.fetched_at.desc())
    return stmt, count_stmt


def purge_copied_fj_articles(session: Session) -> int:
    result = session.execute(delete(Article).where(Article.source_id == FJ_SOURCE_ID))
    session.commit()
    return int(result.rowcount or 0)


def _fj_page(
    *,
    page: int,
    page_size: int,
    offset: int,
    keyword: str | None,
    lang: str | None,
    level: str | None,
    session: Session,
) -> dict:
    if not fj_configured():
        return ok(PageData(items=[], page=page, page_size=page_size, total=0))
    try:
        total = count_fj_news(q=keyword, lang=lang, level=level)
        items = list_fj_news(
            q=keyword, lang=lang, level=level, limit=page_size, offset=offset
        )
    except Exception as exc:  # noqa: BLE001 - 直读失败要给列表页
        return fail(f"FinancialJuice 库不可用: {type(exc).__name__}")
    return ok(PageData(items=apply_title_zh(session, items), page=page, page_size=page_size, total=total))


@router.get("/news")
def news(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    source_id: str | None = None,
    q: str | None = None,
    lang: str | None = None,
    level: str | None = None,
    session: Session = Depends(get_session),
) -> dict:
    offset = (page - 1) * page_size
    keyword = q.strip() if q else None
    level_key = level.strip() if level else None

    if source_id and source_id != FJ_SOURCE_ID and level_key:
        return ok(PageData(items=[], page=page, page_size=page_size, total=0))

    if source_id == FJ_SOURCE_ID or level_key:
        return _fj_page(
            page=page,
            page_size=page_size,
            offset=offset,
            keyword=keyword,
            lang=lang,
            level=level_key,
            session=session,
        )

    stmt, count_stmt = _local_news_query(session, source_id=source_id, q=keyword, lang=lang)
    local_total = session.scalar(count_stmt) or 0

    if source_id or not fj_configured():
        rows = session.scalars(stmt.offset(offset).limit(page_size)).all()
        return ok(
            PageData(
                items=apply_title_zh(session, [_article_out(row) for row in rows]),
                page=page,
                page_size=page_size,
                total=local_total,
            )
        )

    need = offset + page_size
    local_rows = session.scalars(stmt.offset(0).limit(need)).all()
    local_items = [_article_out(row) for row in local_rows]
    try:
        fj_total = count_fj_news(q=keyword, lang=lang)
        fj_items = list_fj_news(q=keyword, lang=lang, limit=need, offset=0)
    except Exception:
        logger.exception("fj live read failed; fallback to local news only")
        rows = session.scalars(stmt.offset(offset).limit(page_size)).all()
        return ok(
            PageData(
                items=apply_title_zh(session, [_article_out(row) for row in rows]),
                page=page,
                page_size=page_size,
                total=local_total,
            )
        )
    items = merge_news_page(local_items, fj_items, offset=offset, limit=page_size)
    return ok(
        PageData(
            items=apply_title_zh(session, items),
            page=page,
            page_size=page_size,
            total=local_total + fj_total,
        )
    )


@router.get("/digest")
async def digest(
    kind: str = Query(default="daily"),
    date: date | None = None,
    session: Session = Depends(get_session),
) -> dict:
    key = kind.strip().lower()
    if key not in {"daily", "weekly"}:
        return fail("kind 只能是 daily 或 weekly")
    try:
        payload = await build_digest(session, kind=key, day=date)
    except Exception as exc:  # noqa: BLE001 - 简报失败要给页面
        logger.exception("digest failed")
        return fail(f"生成简报失败: {type(exc).__name__}")
    return ok(payload.model_dump(mode="json"))


@router.get("/summaries")
def summaries() -> dict:
    return ok({"items": list_summaries()})


@router.get("/summaries/{day}")
def summary_day(day: str) -> dict:
    item = read_summary(day)
    if item is None:
        return fail("没有这份报告")
    return ok(item)


@router.post("/summaries/generate")
async def trigger_generate_summary(
    day: str | None = None,
    overwrite: bool = False,
    session: Session = Depends(get_session),
) -> dict:
    target_date: date | None = None
    if day:
        try:
            target_date = date.fromisoformat(day.strip())
        except ValueError:
            return fail("日期格式不合法，请使用 YYYY-MM-DD")
    try:
        from app.summary_service import generate_daily_summary

        path = await generate_daily_summary(
            target_day=target_date, overwrite=overwrite, session=session
        )
        return ok({"date": path.stem, "filename": path.name})
    except Exception as exc:
        logger.exception("generate summary failed: %s", exc)
        return fail(f"生成日报失败: {exc}")


@router.get("/events")
async def events(
    start: date | None = None,
    end: date | None = None,
    session: Session = Depends(get_session),
) -> dict:
    await ensure_econ_events(session)
    rows = list_events(session, start=start, end=end)
    return ok(apply_event_title_zh(session, [to_event_out(row) for row in rows]))


@router.get("/charts")
def charts(
    board: str = Query(default="github"),
    region: str = Query(default="global"),
    chart: str = Query(default="daily"),
    date: date | None = None,
) -> dict:
    board_key = board.strip().lower()
    region_key = region.strip().lower()
    chart_key = chart.strip().lower()
    if board_key == "github":
        region_key, chart_key = "global", "daily"
    elif board_key == "appstore":
        if region_key not in APPSTORE_REGIONS:
            return fail(f"region 只能是 {', '.join(APPSTORE_REGIONS)}")
        if chart_key not in APPSTORE_CHARTS:
            return fail(f"chart 只能是 {', '.join(APPSTORE_CHARTS)}")
    else:
        return fail("board 只能是 github 或 appstore")
    if not charts_configured():
        return fail("榜单数据库未配置")
    try:
        engine = get_charts_engine()
        snap_date, fetched_at, rows = read_snapshot(
            engine, board=board_key, region=region_key, chart=chart_key, day=date
        )
        dates = list_snap_dates(engine, board=board_key, region=region_key, chart=chart_key)
    except Exception as exc:  # noqa: BLE001 - 榜单库不可用要给页面
        logger.exception("charts read failed")
        return fail(f"榜单数据库不可用: {type(exc).__name__}")
    payload = ChartOut(
        board=board_key,
        region=region_key,
        chart=chart_key,
        snap_date=snap_date.isoformat() if snap_date else None,
        fetched_at=assume_tz(fetched_at, UTC) if fetched_at else None,
        dates=[item.isoformat() for item in dates],
        items=[ChartItemOut.model_validate(row) for row in rows],
    )
    return ok(payload.model_dump(mode="json"))


@router.get("/sources")
def sources(session: Session = Depends(get_session)) -> dict:
    states = {row.source_id: row for row in session.scalars(select(SourceState))}
    items: list[SourceOut] = []
    for source in load_sources():
        state = states.get(source.id)
        items.append(
            SourceOut(
                id=source.id,
                name=source.name,
                region=source.region,
                kind=source.kind,
                access=source.access,
                enabled=source.enabled,
                url=source.url,
                poll_interval_minutes=source.poll_interval_minutes,
                lang=source.lang,
                note=source.note,
                runnable=source.runnable,
                last_fetch_at=state.last_fetch_at if state else None,
                last_status=state.last_status if state else "idle",
                last_error=state.last_error if state else "",
                last_count=state.last_count if state else 0,
            )
        )
    return ok(items)


@router.post("/ingest")
async def ingest(session: Session = Depends(get_session)) -> dict:
    result: IngestResult = await ingest_all(session, force=True)
    return ok(result)


@router.post("/events/sync")
def events_sync(session: Session = Depends(get_session)) -> dict:
    count = sync_cn_events(session)
    return ok({"upserted": count})
