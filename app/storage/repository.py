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

# 当 API 返回 null/缺失时，为 NOT NULL 列提供的回退值。
# 可空列（STID, RID, FCID, STRID, DatePublished）被省略 → 保持 None。
_NOT_NULL_DEFAULTS: dict[str, Any] = {
    "Tags": "[]", "StreamIDs": "[]", "TickerIDs": "[]", "Labels": "[]",
    "Breaking": 0, "HasE": 0,
    "Title": "", "TypeID": "", "Description": "", "PostedShort": "", "PostedLong": "",
    "TestDatePublished": "", "Upd": "", "Img": "", "Level": "", "EURL": "",
    "RURL": "", "EURLImg": "", "FCName": "", "FCNameURL": "", "IID": "",
}


def serialize_raw_value(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


class NewsRepository:
    def __init__(self, connection: sqlite3.Connection, table: str = "news") -> None:
        self.connection = connection
        self.table = table

    def insert_many(self, items: list[NewsItem], source_method: str = "", task_id: int = 0) -> tuple[int, int, list[int], int | None]:
        inserted = 0
        skipped = 0
        new_ids: list[int] = []
        min_skipped_id: int | None = None

        for item in items:
            raw = item.raw_payload
            columns = [*RAW_COLUMNS, "fetched_at", "created_at", "source_method", "task_id"]
            placeholders = ", ".join("?" for _ in columns)
            column_sql = ", ".join(columns)
            values = [
                serialize_raw_value(raw.get(column)) if raw.get(column) is not None
                else _NOT_NULL_DEFAULTS.get(column)
                for column in RAW_COLUMNS
            ]
            fetched_at = item.fetched_at.strftime("%Y-%m-%d %H:%M:%S")
            values.extend([fetched_at, fetched_at, source_method, task_id])
            cursor = self.connection.execute(
                f"INSERT OR IGNORE INTO {self.table} ({column_sql}) VALUES ({placeholders})",
                values,
            )
            if cursor.rowcount:
                inserted += 1
                news_id = raw.get("NewsID")
                if news_id is not None:
                    new_ids.append(int(news_id))
            else:
                skipped += 1
                news_id = raw.get("NewsID")
                if news_id is not None:
                    nid = int(news_id)
                    if min_skipped_id is None or nid < min_skipped_id:
                        min_skipped_id = nid

        self.connection.commit()
        return inserted, skipped, new_ids, min_skipped_id

    def count(self) -> int:
        cursor = self.connection.execute(f"SELECT COUNT(*) FROM {self.table}")
        return int(cursor.fetchone()[0])

    def max_news_id(self) -> int:
        cursor = self.connection.execute(f"SELECT MAX(NewsID) FROM {self.table}")
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def min_news_id(self) -> int:
        cursor = self.connection.execute(f"SELECT MIN(NewsID) FROM {self.table}")
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def find_block_bottom(self, top_id: int, since_iso: str) -> int | None:
        """
        如果 top_id 存在于数据库中，返回其连续数据块的最小 NewsID。
        用于在历史同步时跳过已同步的范围。
        要求 NewsID 随日期单调递增。
        """
        row = self.connection.execute(
            f"SELECT 1 FROM {self.table} WHERE NewsID = ?", (top_id,)
        ).fetchone()
        if not row:
            return None
        row = self.connection.execute(
            f"""
            SELECT MAX(n.NewsID) FROM {self.table} n
            WHERE n.NewsID <= ?
              AND n.DatePublished >= ?
              AND NOT EXISTS (
                  SELECT 1 FROM {self.table} n2
                  WHERE n2.NewsID = n.NewsID - 1
                    AND n2.DatePublished >= ?
              )
            """,
            (top_id, since_iso, since_iso),
        ).fetchone()
        return row[0] if row and row[0] is not None else None
