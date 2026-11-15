from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.fj_read import FJ_SOURCE_ID, fj_configured, list_fj_news
from app.ingest.events import list_events, to_event_out
from app.ingest.fetchers.digest_feeds import event_to_econ_item
from app.ingest.remote_events import ensure_econ_events, ensure_hotspots, list_latest_hotspots
from app.ingest.sources import get_source_map
from app.models import Article
from app.schemas import ArticleOut, DigestHeadline, DigestOut, EconCalendarItem
from app.timeutil import BIZ_ZONE, today_biz
from app.translate import apply_event_title_zh, apply_title_zh

TECH_LIMIT = 20
TECH_FETCH_CAP = 400
TECH_SOURCE_CAP = 5
TECH_BUCKET_SOURCE_CAP = 2
FINANCE_LIMIT = 20


class _StampedRow(Protocol):
    id: int
    source_id: str
    published_at: datetime | None
    fetched_at: datetime


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=BIZ_ZONE)
    return start, start + timedelta(days=1)


def week_bounds(day: date) -> tuple[date, date, datetime, datetime]:
    monday = day - timedelta(days=day.weekday())
    sunday = monday + timedelta(days=6)
    start, _ = day_bounds(monday)
    _, end = day_bounds(sunday)
    return monday, sunday, start, end


def lookahead_dates(kind: str, day: date) -> tuple[date, date]:
    if kind == "weekly":
        monday = day - timedelta(days=day.weekday())
        nxt = monday + timedelta(days=7)
        return nxt, nxt + timedelta(days=6)
    start = day + timedelta(days=1)
    return start, start + timedelta(days=6)


def fj_range_keys(start: datetime, end: datetime) -> tuple[str, str]:
    return (
        start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
        end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
    )


def _format_when(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(BIZ_ZONE).strftime("%m-%d %H:%M")


def _filter_econ(
    items: list[EconCalendarItem],
    start: date,
    end: date,
) -> list[EconCalendarItem]:
    picked: list[EconCalendarItem] = []
    for item in items:
        if start.isoformat() <= item.biz_date <= end.isoformat():
            picked.append(item)
    return picked


def _item_stamp(row: _StampedRow) -> datetime:
    value = row.published_at or row.fetched_at
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _bucket_index(stamp: datetime, start: datetime, end: datetime, buckets: int) -> int:
    start_utc = start.astimezone(UTC)
    end_utc = end.astimezone(UTC)
    total = (end_utc - start_utc).total_seconds()
    if buckets <= 1 or total <= 0:
        return 0
    pos = (stamp.astimezone(UTC) - start_utc).total_seconds()
    idx = int(pos / total * buckets)
    return min(max(idx, 0), buckets - 1)


def pick_tech_rows(
    rows: Sequence[_StampedRow],
    *,
    start: datetime,
    end: datetime,
    limit: int = TECH_LIMIT,
    buckets: int = 4,
    source_cap: int = TECH_SOURCE_CAP,
    bucket_source_cap: int = TECH_BUCKET_SOURCE_CAP,
) -> list[_StampedRow]:
    """按全天时段取材，避免晚上高频源把上午大新闻顶掉。"""
    if not rows or limit <= 0:
        return []
    bucket_n = max(1, buckets)
    grouped: list[list[_StampedRow]] = [[] for _ in range(bucket_n)]
    for row in rows:
        grouped[_bucket_index(_item_stamp(row), start, end, bucket_n)].append(row)
    for bucket in grouped:
        bucket.sort(key=_item_stamp, reverse=True)

    quotas = [limit // bucket_n] * bucket_n
    for i in range(limit % bucket_n):
        quotas[-(i + 1)] += 1

    picked: list[_StampedRow] = []
    used: set[int] = set()
    source_count: dict[str, int] = {}

    def take(candidates: Sequence[_StampedRow], n: int) -> list[_StampedRow]:
        if n <= 0:
            return []
        queues: dict[str, list[_StampedRow]] = {}
        order: list[str] = []
        for row in candidates:
            if row.id in used:
                continue
            sid = row.source_id
            if sid not in queues:
                queues[sid] = []
                order.append(sid)
            queues[sid].append(row)
        got: list[_StampedRow] = []
        local_count: dict[str, int] = {}
        while len(got) < n and any(queues.values()):
            progressed = False
            for sid in order:
                if len(got) >= n:
                    break
                pile = queues.get(sid) or []
                if not pile:
                    continue
                if source_count.get(sid, 0) >= source_cap or local_count.get(sid, 0) >= bucket_source_cap:
                    queues[sid] = []
                    continue
                row = pile.pop(0)
                queues[sid] = pile
                got.append(row)
                used.add(row.id)
                source_count[sid] = source_count.get(sid, 0) + 1
                local_count[sid] = local_count.get(sid, 0) + 1
                progressed = True
            if not progressed:
                break
        return got

    leftover = 0
    for bucket, quota in zip(grouped, quotas, strict=True):
        got = take(bucket, quota)
        picked.extend(got)
        leftover += quota - len(got)
    if leftover > 0:
        rest = [row for row in sorted(rows, key=_item_stamp, reverse=True) if row.id not in used]
        picked.extend(take(rest, leftover))
    picked.sort(key=_item_stamp, reverse=True)
    return picked[:limit]


def _local_tech(
    session: Session,
    start: datetime,
    end: datetime,
    *,
    buckets: int,
) -> list[DigestHeadline]:
    start_utc = start.astimezone(UTC)
    end_utc = end.astimezone(UTC)
    stamp = func.coalesce(Article.published_at, Article.fetched_at)
    rows = session.scalars(
        select(Article)
        .where(Article.source_id != FJ_SOURCE_ID)
        .where(stamp >= start_utc)
        .where(stamp < end_utc)
        .order_by(stamp.desc())
        .limit(TECH_FETCH_CAP)
    ).all()
    rows = pick_tech_rows(rows, start=start, end=end, buckets=buckets)
    names = get_source_map()
    outs = []
    for row in rows:
        title = (row.title_zh or "").strip() or row.title
        outs.append(
            ArticleOut(
                id=row.id,
                source_id=row.source_id,
                source_name=names.get(row.source_id).name if names.get(row.source_id) else row.source_id,
                title=title,
                url=row.url,
                summary="",
                published_at=row.published_at,
                fetched_at=row.fetched_at,
                lang=row.lang,
                kind=row.kind,
            )
        )
    apply_title_zh(session, outs)
    items: list[DigestHeadline] = []
    for item in outs:
        items.append(
            DigestHeadline(
                time=_format_when(item.published_at or item.fetched_at),
                title=item.title,
                source=item.source_name,
                url=item.url,
            )
        )
    return items


def _finance_items(session: Session, start: datetime, end: datetime) -> list[DigestHeadline]:
    if not fj_configured():
        return []
    published_from, published_to = fj_range_keys(start, end)
    merged: list = []
    for level in ("active active-critical", "active"):
        merged.extend(
            list_fj_news(
                level=level,
                published_from=published_from,
                published_to=published_to,
                limit=FINANCE_LIMIT,
                offset=0,
            )
        )
    merged.sort(key=lambda item: item.published_at or item.fetched_at, reverse=True)
    merged = apply_title_zh(session, merged[:FINANCE_LIMIT])
    items: list[DigestHeadline] = []
    for row in merged:
        extra = "重要" if "critical" in row.level else "数据"
        items.append(
            DigestHeadline(
                time=_format_when(row.published_at),
                title=row.title,
                source=row.source_name,
                url=row.url,
                origin=row.origin,
                extra=extra,
            )
        )
    return items


def _overview(
    *,
    kind: str,
    day: date,
    range_start: date,
    range_end: date,
    tech_n: int,
    finance_n: int,
    hot_n: int,
    finance_items: list[DigestHeadline],
) -> str:
    if kind == "weekly":
        head = f"{range_start.isoformat()} 至 {range_end.isoformat()} 周报"
    else:
        head = f"{day.isoformat()} 日报"
    parts = [f"科技 {tech_n} 条", f"金融重要/数据 {finance_n} 条", f"热搜 {hot_n} 条"]
    text = f"{head}：{'，'.join(parts)}。"
    tops = [item.title for item in finance_items[:3] if item.extra == "重要"]
    if tops:
        text += "要闻：" + "；".join(tops) + "。"
    return text


async def build_digest(session: Session, *, kind: str, day: date | None) -> DigestOut:
    target = day or today_biz()
    if kind == "weekly":
        range_start, range_end, start, end = week_bounds(target)
    else:
        range_start = range_end = target
        start, end = day_bounds(target)
    look_start, look_end = lookahead_dates(kind, target)
    econ_start = look_start if kind == "weekly" else target

    tech_items = _local_tech(session, start, end, buckets=7 if kind == "weekly" else 4)
    finance_items = _finance_items(session, start, end)

    await ensure_hotspots(session)
    await ensure_econ_events(session)
    hotspot_items = list_latest_hotspots(session)
    warnings: list[str] = []
    if not hotspot_items:
        warnings.append("微博热搜暂无本地快照")

    query_start = min(econ_start, look_start)
    event_rows = list_events(session, start=query_start, end=look_end)
    tech_events = [
        row for row in event_rows if row.category != "econ" and look_start <= row.start_date <= look_end
    ]
    econ_rows = [row for row in event_rows if row.category == "econ"]
    econ_outs = apply_event_title_zh(session, [to_event_out(row) for row in econ_rows])
    econ_all = [event_to_econ_item(item) for item in econ_outs]
    econ_calendar = _filter_econ(econ_all, econ_start, look_end)
    if econ_all and not econ_calendar:
        warnings.append("所选前瞻窗口内暂无已落地的高/中影响数据")

    next_events = apply_event_title_zh(session, [to_event_out(row) for row in tech_events])

    return DigestOut(
        kind=kind,
        date=target.isoformat(),
        range_start=range_start.isoformat(),
        range_end=range_end.isoformat(),
        lookahead_start=look_start.isoformat(),
        lookahead_end=look_end.isoformat(),
        timezone="Asia/Shanghai",
        is_sunday=target.weekday() == 6,
        overview=_overview(
            kind=kind,
            day=target,
            range_start=range_start,
            range_end=range_end,
            tech_n=len(tech_items),
            finance_n=len(finance_items),
            hot_n=len(hotspot_items),
            finance_items=finance_items,
        ),
        tech_items=tech_items,
        finance_items=finance_items,
        hotspot_items=hotspot_items,
        next_events=next_events,
        econ_calendar=econ_calendar,
        warnings=warnings,
    )
