import json
import sqlite3
from typing import Any

from app.models import NewsItem


RAW_COLUMNS = [
    "Tags",
    "STID",
    "NewsID",
    "Title",
    "TypeID",
    "Description",
    "PostedShort",
    "PostedLong",
    "DatePublished",
    "TestDatePublished",
    "Breaking",
    "Upd",
    "Img",
    "Level",
    "EURL",
    "HasE",
    "RURL",
    "EURLImg",
    "STRID",
    "RID",
    "FCID",
    "FCName",
    "FCNameURL",
    "StreamIDs",
    "TickerIDs",
    "Labels",
    "IID",
]


def serialize_raw_value(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


class NewsRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def insert_many(self, items: list[NewsItem]) -> tuple[int, int]:
        inserted = 0
        skipped = 0

        for item in items:
            raw = item.raw_payload
            columns = [*RAW_COLUMNS, "fetched_at", "raw_json"]
            placeholders = ", ".join("?" for _ in columns)
            column_sql = ", ".join(columns)
            values = [
                serialize_raw_value(raw.get(column))
                for column in RAW_COLUMNS
            ]
            values.extend(
                [
                    item.fetched_at.isoformat(),
                    json.dumps(raw, ensure_ascii=False),
                ]
            )
            cursor = self.connection.execute(
                f"INSERT OR IGNORE INTO news ({column_sql}) VALUES ({placeholders})",
                values,
            )
            if cursor.rowcount:
                inserted += 1
            else:
                skipped += 1

        self.connection.commit()
        return inserted, skipped

    def count(self) -> int:
        cursor = self.connection.execute("SELECT COUNT(*) FROM news")
        return int(cursor.fetchone()[0])
