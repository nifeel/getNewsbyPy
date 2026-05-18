from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class NewsItem(BaseModel):
    external_id: str
    title: str
    content: str = ""
    source: str = "FinancialJuice"
    category: str = ""
    url: str = ""
    published_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    raw_payload: dict[str, Any]
