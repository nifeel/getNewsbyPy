from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field


CHINA_TIME_ZONE = ZoneInfo("Asia/Shanghai")


def china_now() -> datetime:
    return datetime.now(CHINA_TIME_ZONE).replace(tzinfo=None)


class NewsItem(BaseModel):
    external_id: str
    title: str
    content: str = ""
    source: str = "FinancialJuice"
    category: str = ""
    url: str = ""
    published_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=china_now)
    raw_payload: dict[str, Any]
