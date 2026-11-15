from datetime import date
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event
from app.schemas import EventOut
from app.timeutil import utc_now

EVENTS_FILE = Path(__file__).resolve().parent.parent / "data" / "events_cn.yaml"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def sync_cn_events(session: Session) -> int:
    raw = yaml.safe_load(EVENTS_FILE.read_text(encoding="utf-8"))
    upserted = 0
    now = utc_now()
    for row in raw.get("events", []):
        event_id = row["id"]
        existing = session.get(Event, event_id)
        start = _parse_date(row["start_date"])
        if start is None:
            continue
        payload = {
            "title": row["title"],
            "start_date": start,
            "end_date": _parse_date(row.get("end_date")),
            "location": row.get("location") or "",
            "url": row.get("url") or "",
            "status": row.get("status") or "estimated",
            "category": row.get("category") or "conference",
            "note": row.get("note") or "",
            "timezone": "Asia/Shanghai",
            "source": "seed_cn",
            "updated_at": now,
        }
        if existing is None:
            session.add(Event(id=event_id, **payload))
        else:
            for key, value in payload.items():
                setattr(existing, key, value)
        upserted += 1
    session.commit()
    return upserted


def list_events(session: Session, start: date | None = None, end: date | None = None) -> list[Event]:
    stmt = select(Event).order_by(Event.start_date.asc(), Event.time.asc(), Event.id.asc())
    if start is not None:
        stmt = stmt.where(Event.start_date >= start)
    if end is not None:
        stmt = stmt.where(Event.start_date <= end)
    return list(session.scalars(stmt))


def to_event_out(row: Event) -> EventOut:
    return EventOut(
        id=row.id,
        title=row.title,
        start_date=row.start_date.isoformat(),
        end_date=row.end_date.isoformat() if row.end_date else None,
        location=row.location,
        url=row.url,
        status=row.status,
        category=row.category,
        note=row.note,
        timezone=row.timezone,
        source=row.source,
        time=row.time or "",
    )
