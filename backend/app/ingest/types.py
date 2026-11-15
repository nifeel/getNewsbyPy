from dataclasses import dataclass
from datetime import datetime


@dataclass
class RawItem:
    title: str
    url: str
    summary: str
    published_at: datetime | None
    lang: str | None = None
    replace_existing: bool = False


class ProxyRequiredError(RuntimeError):
    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        super().__init__(f"source {source_id} requires proxy; skipped in cn-only ingest")
