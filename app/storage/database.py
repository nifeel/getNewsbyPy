import sqlite3
from pathlib import Path

from app.browser.session import ensure_parent_dir


SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    Tags TEXT NOT NULL DEFAULT '[]',
    STID INTEGER,
    NewsID INTEGER NOT NULL,
    Title TEXT NOT NULL DEFAULT '',
    TypeID TEXT NOT NULL DEFAULT '',
    Description TEXT NOT NULL DEFAULT '',
    PostedShort TEXT NOT NULL DEFAULT '',
    PostedLong TEXT NOT NULL DEFAULT '',
    DatePublished TEXT,
    TestDatePublished TEXT NOT NULL DEFAULT '',
    Breaking INTEGER NOT NULL DEFAULT 0,
    Upd TEXT NOT NULL DEFAULT '',
    Img TEXT NOT NULL DEFAULT '',
    Level TEXT NOT NULL DEFAULT '',
    EURL TEXT NOT NULL DEFAULT '',
    HasE INTEGER NOT NULL DEFAULT 0,
    RURL TEXT NOT NULL DEFAULT '',
    EURLImg TEXT NOT NULL DEFAULT '',
    STRID INTEGER,
    RID INTEGER,
    FCID INTEGER,
    FCName TEXT NOT NULL DEFAULT '',
    FCNameURL TEXT NOT NULL DEFAULT '',
    StreamIDs TEXT NOT NULL DEFAULT '[]',
    TickerIDs TEXT NOT NULL DEFAULT '[]',
    Labels TEXT NOT NULL DEFAULT '[]',
    IID TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_news_news_id
ON news(NewsID);

CREATE INDEX IF NOT EXISTS ix_news_published_at
ON news(DatePublished);

CREATE TABLE IF NOT EXISTS task_status (
    task_name TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    cycle INTEGER NOT NULL DEFAULT 0,
    inserted INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    pages INTEGER NOT NULL DEFAULT 0,
    target_since TEXT,
    current_old_id TEXT NOT NULL DEFAULT '',
    current_oldest_at TEXT,
    message TEXT NOT NULL DEFAULT '',
    pid INTEGER,
    last_started_at TEXT,
    last_finished_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gui_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


TASK_STATUS_COLUMNS = {
    "pages": "INTEGER NOT NULL DEFAULT 0",
    "target_since": "TEXT",
    "current_old_id": "TEXT NOT NULL DEFAULT ''",
    "current_oldest_at": "TEXT",
    "message": "TEXT NOT NULL DEFAULT ''",
}


GUI_SETTING_DEFAULTS = {
    "history_since": "",
    "history_interval_seconds": "60",
}


NEWS_COLUMNS = {
    "id",
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
    "fetched_at",
    "created_at",
}


def connect(database_path: Path) -> sqlite3.Connection:
    ensure_parent_dir(database_path)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(connection: sqlite3.Connection) -> None:
    reset_legacy_news_table(connection)
    connection.executescript(SCHEMA)
    ensure_task_status_columns(connection)
    ensure_gui_settings(connection)
    connection.commit()


def reset_legacy_news_table(connection: sqlite3.Connection) -> None:
    cursor = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'news'")
    if cursor.fetchone() is None:
        return

    columns = {row["name"] if isinstance(row, sqlite3.Row) else row[1] for row in connection.execute("PRAGMA table_info(news)")}
    if columns == NEWS_COLUMNS:
        return

    connection.execute("DROP TABLE news")


def ensure_task_status_columns(connection: sqlite3.Connection) -> None:
    cursor = connection.execute("PRAGMA table_info(task_status)")
    existing = {row["name"] if isinstance(row, sqlite3.Row) else row[1] for row in cursor.fetchall()}
    for column, definition in TASK_STATUS_COLUMNS.items():
        if column not in existing:
            connection.execute(f"ALTER TABLE task_status ADD COLUMN {column} {definition}")


def ensure_gui_settings(connection: sqlite3.Connection) -> None:
    for key, value in GUI_SETTING_DEFAULTS.items():
        connection.execute(
            """
            INSERT OR IGNORE INTO gui_settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
            (key, value),
        )
