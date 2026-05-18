import json
from datetime import datetime
from typing import Any

from app.models import NewsItem


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    with_tz = normalized.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(with_tz)
    except ValueError:
        return None


def parse_startup_payload(payload: dict[str, Any]) -> list[NewsItem]:
    raw_d = payload.get("d")
    if isinstance(raw_d, str):
        decoded = json.loads(raw_d)
    elif isinstance(raw_d, dict):
        decoded = raw_d
    else:
        decoded = payload

    raw_news = decoded.get("News", [])
    if not isinstance(raw_news, list):
        return []

    items: list[NewsItem] = []
    for raw_item in raw_news:
        if not isinstance(raw_item, dict):
            continue

        title = str(raw_item.get("Title") or "").strip()
        news_id = raw_item.get("NewsID")
        if not title or news_id is None:
            continue

        published_at = parse_datetime(raw_item.get("DatePublished"))
        source = str(raw_item.get("FCName") or "FinancialJuice").strip() or "FinancialJuice"
        content = str(raw_item.get("Description") or "").strip()
        level = str(raw_item.get("Level") or "").strip()

        items.append(
            NewsItem(
                external_id=str(news_id),
                title=title,
                content=content,
                source=source,
                category=level,
                url=str(raw_item.get("EURL") or "").strip(),
                published_at=published_at,
                raw_payload=raw_item,
            )
        )

    return items
