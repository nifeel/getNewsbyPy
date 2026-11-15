import json
import logging
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.ingest.events import to_event_out
from app.ingest.fetchers.digest_feeds import (
    econ_to_event,
    event_row_payload,
    fetch_econ_calendar,
    fetch_weibo_hot,
)
from app.ingest.http_client import fetch_content
from app.ingest.sources import SourceConfig
from app.models import Event, HotItem
from app.schemas import DigestHeadline
from app.timeutil import today_biz, utc_now
from app.translate import apply_event_title_zh

logger = logging.getLogger(__name__)
FF_SOURCE = "forex-factory-calendar"
WEIBO_SOURCE = "weibo-hot"


def _parse_techconf(payload: object) -> list[dict]:
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    today = today_biz()
    events: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        conf_id = str(row.get("id") or "").strip()
        title = str(row.get("title") or "").strip()
        upcoming = row.get("upcoming_events") or {}
        if not conf_id or not title or not isinstance(upcoming, dict):
            continue
        for year, info in upcoming.items():
            if not isinstance(info, dict):
                continue
            dates = info.get("dates") or {}
            start_text = str(dates.get("start") or "").strip()
            if not start_text:
                continue
            start = date.fromisoformat(start_text)
            if start < today:
                continue
            end_text = str(dates.get("end") or "").strip()
            location = info.get("location") or {}
            city = location.get("city") if isinstance(location, dict) else ""
            country = location.get("country") if isinstance(location, dict) else ""
            place = " ".join(part for part in [str(city or ""), str(country or "")] if part).strip()
            fmt = str(info.get("format") or "")
            events.append(
                {
                    "id": f"techconf-{conf_id}-{year}",
                    "title": title,
                    "start_date": start,
                    "end_date": date.fromisoformat(end_text) if end_text else None,
                    "location": place,
                    "url": f"https://techconf.directory/conferences/{conf_id}",
                    "status": "confirmed",
                    "category": "conference",
                    "note": fmt,
                    "timezone": "UTC",
                    "source": "techconf-directory",
                }
            )
    return events


def upsert_events(session: Session, rows: list[dict]) -> int:
    now = utc_now()
    count = 0
    deduped: dict[str, dict] = {}
    for row in rows:
        deduped[row["id"]] = row

    for event_id, payload in deduped.items():
        existing = session.get(Event, event_id)
        data = {**payload, "updated_at": now}
        data.pop("id", None)
        if existing is None:
            session.add(Event(id=event_id, **data))
        else:
            for key, value in data.items():
                setattr(existing, key, value)
        count += 1
    session.commit()
    return count


async def sync_remote_events(session: Session, source: SourceConfig) -> int:
    content, _via = await fetch_content(
        source.fetch_url,
        prefer_proxy=True,
        timeout_seconds=settings.event_http_timeout_seconds,
    )
    payload = json.loads(content.decode("utf-8"))
    if source.id == "techconf-directory":
        rows = _parse_techconf(payload)
    else:
        rows = []
    return upsert_events(session, rows)


async def sync_econ_calendar(session: Session) -> int:
    items, err = await fetch_econ_calendar()
    if err:
        raise RuntimeError(err)
    count = upsert_events(session, [event_row_payload(econ_to_event(item)) for item in items])
    rows = session.scalars(select(Event).where(Event.source == FF_SOURCE)).all()
    apply_event_title_zh(session, [to_event_out(row) for row in rows])
    return count


async def ensure_econ_events(session: Session) -> None:
    exists = session.scalar(select(Event.id).where(Event.source == FF_SOURCE).limit(1))
    if exists is not None:
        return
    try:
        await sync_econ_calendar(session)
    except Exception:
        logger.exception("ensure econ calendar failed")


async def sync_weibo_hot(session: Session) -> int:
    items, err = await fetch_weibo_hot()
    if err:
        raise RuntimeError(err)
    now = utc_now()
    for item in items:
        session.add(
            HotItem(
                source_id=WEIBO_SOURCE,
                title=item.title,
                url=item.url,
                extra=item.extra,
                batch_at=now,
            )
        )
    session.commit()
    return len(items)


async def ensure_hotspots(session: Session) -> None:
    exists = session.scalar(select(HotItem.id).limit(1))
    if exists is not None:
        return
    try:
        await sync_weibo_hot(session)
    except Exception:
        logger.exception("ensure weibo hotspot failed")


def list_latest_hotspots(session: Session) -> list[DigestHeadline]:
    latest = session.scalar(select(func.max(HotItem.batch_at)).where(HotItem.source_id == WEIBO_SOURCE))
    if latest is None:
        return []
    rows = session.scalars(
        select(HotItem)
        .where(HotItem.source_id == WEIBO_SOURCE, HotItem.batch_at == latest)
        .order_by(HotItem.id.asc())
    ).all()
    return [
        DigestHeadline(title=row.title, source="微博热搜", url=row.url, extra=row.extra) for row in rows
    ]
