from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from app.ingest.http_client import build_client
from app.schemas import DigestHeadline, EconCalendarItem, EventOut
from app.timeutil import BIZ_ZONE
from app.translate import source_hash

FF_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
WEIBO_HOT_URL = "https://weibo.com/ajax/side/hotSearch"
NY_ZONE = ZoneInfo("America/New_York")

WATCH_COUNTRIES = {"USD", "CNY", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"}
WATCH_IMPACT = {"High", "Medium"}
COUNTRY_LABEL = {
    "USD": "美国",
    "CNY": "中国",
    "EUR": "欧元区",
    "GBP": "英国",
    "JPY": "日本",
    "AUD": "澳大利亚",
    "NZD": "新西兰",
    "CAD": "加拿大",
    "CHF": "瑞士",
}


def parse_ff_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=NY_ZONE)
    return parsed.astimezone(BIZ_ZONE)


def parse_econ_calendar(payload: object) -> list[EconCalendarItem]:
    if not isinstance(payload, list):
        return []
    items: list[EconCalendarItem] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        country = str(raw.get("country") or "").strip().upper()
        impact = str(raw.get("impact") or "").strip()
        title = str(raw.get("title") or "").strip()
        if not title or country not in WATCH_COUNTRIES or impact not in WATCH_IMPACT:
            continue
        when = parse_ff_datetime(raw.get("date"))
        if when is None:
            continue
        items.append(
            EconCalendarItem(
                time=when.strftime("%m-%d %H:%M"),
                biz_date=when.strftime("%Y-%m-%d"),
                title=title,
                country=COUNTRY_LABEL.get(country, country),
                impact="高" if impact == "High" else "中",
                forecast=str(raw.get("forecast") or "").strip(),
                previous=str(raw.get("previous") or "").strip(),
                actual=str(raw.get("actual") or "").strip(),
            )
        )
    items.sort(key=lambda item: item.time)
    return items


def econ_to_event(item: EconCalendarItem) -> EventOut:
    clock = item.time.split(" ", 1)[-1] if " " in item.time else item.time
    clock_slug = clock.replace(":", "") if clock else "0000"
    bits = [f"影响{item.impact}"]
    if item.forecast:
        bits.append(f"预期 {item.forecast}")
    if item.previous:
        bits.append(f"前值 {item.previous}")
    if item.actual:
        bits.append(f"公布 {item.actual}")
    return EventOut(
        id=f"ff-{item.biz_date}-{item.country}-{clock_slug}-{source_hash(item.title)}",
        title=item.title,
        start_date=item.biz_date,
        end_date=None,
        location=item.country,
        url="",
        status="confirmed",
        category="econ",
        note=" · ".join(bits),
        timezone="Asia/Shanghai",
        source="forex-factory-calendar",
        time=clock,
    )


def event_row_payload(item: EventOut) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "start_date": date.fromisoformat(item.start_date),
        "end_date": date.fromisoformat(item.end_date) if item.end_date else None,
        "location": item.location,
        "url": item.url,
        "status": item.status,
        "category": item.category,
        "note": item.note,
        "timezone": item.timezone,
        "source": item.source,
        "time": item.time,
    }


def _note_value(note: str, label: str) -> str:
    prefix = f"{label} "
    for part in note.split(" · "):
        if part.startswith(prefix):
            return part[len(prefix) :].strip()
    return ""


def event_to_econ_item(item: EventOut) -> EconCalendarItem:
    day = date.fromisoformat(item.start_date)
    clock = item.time or "00:00"
    if "影响高" in item.note:
        impact = "高"
    elif "影响中" in item.note:
        impact = "中"
    else:
        impact = ""
    return EconCalendarItem(
        time=f"{day.strftime('%m-%d')} {clock}",
        biz_date=item.start_date,
        title=item.title,
        country=item.location,
        impact=impact,
        forecast=_note_value(item.note, "预期"),
        previous=_note_value(item.note, "前值"),
        actual=_note_value(item.note, "公布"),
    )


def parse_weibo_hot(payload: object, *, limit: int = 15) -> list[DigestHeadline]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    rows = data.get("realtime") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    items: list[DigestHeadline] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        if raw.get("is_ad"):
            continue
        title = str(raw.get("note") or raw.get("word") or "").strip()
        if not title:
            continue
        word = str(raw.get("word") or title).strip()
        extra = str(raw.get("icon_desc") or "").strip()
        items.append(
            DigestHeadline(
                title=title,
                source="微博热搜",
                url=f"https://s.weibo.com/weibo?q={quote(word)}",
                extra=extra,
            )
        )
        if len(items) >= limit:
            break
    return items


def _json_loads(content: bytes) -> Any:
    return json.loads(content.decode("utf-8"))


async def fetch_econ_calendar() -> tuple[list[EconCalendarItem], str]:
    try:
        async with build_client(via_proxy=True) as client:
            response = await client.get(FF_CALENDAR_URL)
            response.raise_for_status()
            items = parse_econ_calendar(_json_loads(response.content))
        return items, ""
    except Exception as exc:  # noqa: BLE001 - 简报辅源失败写入 warnings
        return [], f"经济日历暂不可用: {type(exc).__name__}"


async def fetch_weibo_hot() -> tuple[list[DigestHeadline], str]:
    headers = {"Referer": "https://weibo.com/", "User-Agent": "Mozilla/5.0"}
    try:
        async with build_client(via_proxy=False) as client:
            response = await client.get(WEIBO_HOT_URL, headers=headers)
            response.raise_for_status()
            items = parse_weibo_hot(_json_loads(response.content))
        if not items:
            return [], "微博热搜暂无条目"
        return items, ""
    except Exception as exc:  # noqa: BLE001 - 简报辅源失败写入 warnings
        return [], f"微博热搜暂不可用: {type(exc).__name__}"
