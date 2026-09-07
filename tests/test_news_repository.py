import sqlite3

from app.models import NewsItem
from app.storage.database import SCHEMA
from app.storage.repository import NewsRepository


def _conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


def _item(news_id: int, title: str, *, level: str = "", description: str = "", breaking: int = 0) -> NewsItem:
    return NewsItem(
        external_id=str(news_id),
        title=title,
        raw_payload={
            "NewsID": news_id,
            "Title": title,
            "Level": level,
            "Description": description,
            "Breaking": breaking,
        },
    )


def test_insert_many_updates_level_without_clearing_title_zh() -> None:
    conn = _conn()
    repo = NewsRepository(conn)
    inserted, skipped, new_ids, min_skipped, translate_ids = repo.insert_many([
        _item(11, "Hello", level="news-general"),
    ])
    assert inserted == 1
    assert skipped == 0
    assert new_ids == [11]
    assert translate_ids == [11]
    conn.execute("UPDATE news SET title_zh = ? WHERE NewsID = ?", ("你好", 11))
    conn.commit()

    inserted, skipped, new_ids, min_skipped, translate_ids = repo.insert_many([
        _item(11, "Hello", level="active active-critical", description="more", breaking=1),
    ])
    assert inserted == 0
    assert skipped == 1
    assert new_ids == []
    assert min_skipped == 11
    assert translate_ids == []
    row = conn.execute("SELECT Title, title_zh, Level, Description, Breaking FROM news WHERE NewsID = 11").fetchone()
    assert row["Title"] == "Hello"
    assert row["title_zh"] == "你好"
    assert row["Level"] == "active active-critical"
    assert row["Description"] == "more"
    assert row["Breaking"] == 1


def test_insert_many_nulls_title_zh_when_title_changes() -> None:
    conn = _conn()
    repo = NewsRepository(conn)
    repo.insert_many([_item(11, "Hello")])
    conn.execute("UPDATE news SET title_zh = ? WHERE NewsID = ?", ("你好", 11))
    conn.commit()

    inserted, skipped, new_ids, min_skipped, translate_ids = repo.insert_many([
        _item(11, "Hello world"),
    ])
    assert inserted == 0
    assert skipped == 1
    assert new_ids == []
    assert translate_ids == [11]
    row = conn.execute("SELECT Title, title_zh FROM news WHERE NewsID = 11").fetchone()
    assert row["Title"] == "Hello world"
    assert row["title_zh"] is None
