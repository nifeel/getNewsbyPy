import sqlite3
from pathlib import Path

from app.browser.session import ensure_parent_dir
from app.storage.task_status import cst_now


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
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
    source_method TEXT NOT NULL DEFAULT '',
    task_id INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_news_news_id
ON news(NewsID);

CREATE INDEX IF NOT EXISTS ix_news_published_at
ON news(DatePublished);

CREATE INDEX IF NOT EXISTS ix_news_fetched_at
ON news(fetched_at);

CREATE INDEX IF NOT EXISTS ix_news_source_method
ON news(source_method);

CREATE TABLE IF NOT EXISTS login_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
);

CREATE TABLE IF NOT EXISTS gui_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type TEXT NOT NULL,
    start_news_id INTEGER,
    current_news_id INTEGER,
    end_news_id INTEGER,
    end_time TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    pid INTEGER,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    cycle INTEGER NOT NULL DEFAULT 0,
    inserted INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    pages INTEGER NOT NULL DEFAULT 0,
    current_old_id TEXT NOT NULL DEFAULT '',
    current_oldest_at TEXT,
    message TEXT NOT NULL DEFAULT '',
    last_started_at TEXT,
    last_finished_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_tasks_type_status
ON tasks(task_type, status);

CREATE TABLE IF NOT EXISTS task_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    task_type TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT 'INFO',
    message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
);

CREATE INDEX IF NOT EXISTS ix_task_logs_task_id
ON task_logs(task_id);

CREATE INDEX IF NOT EXISTS ix_task_logs_created_at
ON task_logs(created_at);

CREATE TABLE IF NOT EXISTS task_status (
    task_name TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT '',
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
    updated_at TEXT NOT NULL DEFAULT ''
);
"""


TASK_DISPLAY_COLUMNS = {
    "cycle": "INTEGER NOT NULL DEFAULT 0",
    "inserted": "INTEGER NOT NULL DEFAULT 0",
    "skipped": "INTEGER NOT NULL DEFAULT 0",
    "total": "INTEGER NOT NULL DEFAULT 0",
    "pages": "INTEGER NOT NULL DEFAULT 0",
    "current_old_id": "TEXT NOT NULL DEFAULT ''",
    "current_oldest_at": "TEXT",
    "message": "TEXT NOT NULL DEFAULT ''",
    "last_started_at": "TEXT",
    "last_finished_at": "TEXT",
}


GUI_SETTING_DEFAULTS = {
    "history_since": "",
    "login_status": "unknown",
    "robot_pid": "",
}

NEWS_SOURCE_METHOD_COLUMNS = ("news",)

# source_method 枚举值 — 用于 news.source_method 列
SOURCE_METHOD_STARTUP = "Startup"
SOURCE_METHOD_HISTORY = "GetPreviousNews"
SOURCE_METHOD_BROWSER = "browser"
SOURCE_METHOD_BROWSER_WS = "browser_ws"


def connect(database_path: Path) -> sqlite3.Connection:
    ensure_parent_dir(database_path)
    connection = sqlite3.connect(database_path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


_db_initialized = False


def init_db(connection: sqlite3.Connection) -> None:
    global _db_initialized
    if _db_initialized:
        return
    connection.executescript("DROP TABLE IF EXISTS sync_tasks;")
    connection.executescript("DROP TABLE IF EXISTS browser_news;")
    connection.executescript(SCHEMA)
    ensure_task_columns(connection)
    ensure_news_columns(connection)
    ensure_gui_settings(connection)
    # 迁移现有 NULL task_id 为 0
    connection.execute("UPDATE news SET task_id = 0 WHERE task_id IS NULL")
    connection.commit()
    _db_initialized = True


def ensure_task_columns(connection: sqlite3.Connection) -> None:
    cursor = connection.execute("PRAGMA table_info(tasks)")
    existing = {row["name"] if isinstance(row, sqlite3.Row) else row[1] for row in cursor.fetchall()}
    for column, definition in TASK_DISPLAY_COLUMNS.items():
        if column not in existing:
            connection.execute(f"ALTER TABLE tasks ADD COLUMN {column} {definition}")


def ensure_news_columns(connection: sqlite3.Connection) -> None:
    for table in NEWS_SOURCE_METHOD_COLUMNS:
        cursor = connection.execute(f"PRAGMA table_info({table})")
        existing = {row["name"] if isinstance(row, sqlite3.Row) else row[1] for row in cursor.fetchall()}
        for column, definition in [
            ("source_method", "TEXT NOT NULL DEFAULT ''"),
            ("task_id", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            if column not in existing:
                try:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc):
                        raise


def ensure_gui_settings(connection: sqlite3.Connection) -> None:
    for key, value in GUI_SETTING_DEFAULTS.items():
        connection.execute(
            """
            INSERT OR IGNORE INTO gui_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            """,
            (key, value, cst_now()),
        )
