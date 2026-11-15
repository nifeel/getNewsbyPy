import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.browser.session import ensure_parent_dir
from app.storage.task_status import cst_now


SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    Tags TEXT NOT NULL DEFAULT '[]',
    STID INTEGER,
    NewsID INTEGER NOT NULL,
    Title TEXT NOT NULL DEFAULT '',
    title_zh TEXT,
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
    "cookie_expires_at": "",
    "tmt_key_index": "0",
    "tmt_key_month": "",
}

NEWS_SOURCE_METHOD_COLUMNS = ("news",)

# SELECT * 在 PostgreSQL 里会变成小写列名；GUI 仍按 SQLite 的驼峰名取值
NEWS_CANONICAL_KEYS = (
    "Tags", "STID", "NewsID", "Title", "title_zh", "TypeID", "Description",
    "PostedShort", "PostedLong", "DatePublished", "TestDatePublished",
    "Breaking", "Upd", "Img", "Level", "EURL", "HasE", "RURL", "EURLImg",
    "STRID", "RID", "FCID", "FCName", "FCNameURL", "StreamIDs", "TickerIDs",
    "Labels", "IID",
)

SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
    Tags TEXT NOT NULL DEFAULT '[]',
    STID INTEGER,
    NewsID INTEGER NOT NULL,
    Title TEXT NOT NULL DEFAULT '',
    title_zh TEXT,
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
    fetched_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    source_method TEXT NOT NULL DEFAULT '',
    task_id INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_news_news_id ON news(NewsID);
CREATE INDEX IF NOT EXISTS ix_news_published_at ON news(DatePublished);
CREATE INDEX IF NOT EXISTS ix_news_fetched_at ON news(fetched_at);
CREATE INDEX IF NOT EXISTS ix_news_source_method ON news(source_method);

CREATE TABLE IF NOT EXISTS login_events (
    id INTEGER PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
    event TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS gui_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
    task_type TEXT NOT NULL,
    start_news_id INTEGER,
    current_news_id INTEGER,
    end_news_id INTEGER,
    end_time TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    pid INTEGER,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
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

CREATE INDEX IF NOT EXISTS ix_tasks_type_status ON tasks(task_type, status);

CREATE TABLE IF NOT EXISTS task_logs (
    id INTEGER PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
    task_id INTEGER,
    task_type TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT 'INFO',
    message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_task_logs_task_id ON task_logs(task_id);
CREATE INDEX IF NOT EXISTS ix_task_logs_created_at ON task_logs(created_at);

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

# source_method 枚举值 — 用于 news.source_method 列
SOURCE_METHOD_STARTUP = "Startup"
SOURCE_METHOD_HISTORY = "GetPreviousNews"
SOURCE_METHOD_BROWSER = "browser"
SOURCE_METHOD_BROWSER_WS = "browser_ws"


class DictRow(Mapping[str, Any]):
    """PostgreSQL dict_row 的大小写不敏感包装，兼容 sqlite3.Row 取值。"""

    def __init__(self, mapping: Mapping[str, Any]) -> None:
        self._data = dict(mapping)
        self._lower = {str(key).lower(): key for key in self._data}

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return list(self._data.values())[key]
        if key in self._data:
            return self._data[key]
        real = self._lower.get(str(key).lower())
        if real is None:
            raise KeyError(key)
        return self._data[real]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def to_dict(self) -> dict[str, Any]:
        out = dict(self._data)
        for canon in NEWS_CANONICAL_KEYS:
            if canon.lower() in self._lower:
                out[canon] = self[canon]
        return out


class PgCursor:
    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor
        self.rowcount = cursor.rowcount
        self.lastrowid = getattr(cursor, "lastrowid", None)

    def fetchone(self) -> DictRow | None:
        row = self._cursor.fetchone()
        return None if row is None else DictRow(row)

    def fetchall(self) -> list[DictRow]:
        return [DictRow(row) for row in self._cursor.fetchall()]


def adapt_sqlite_sql(sql: str) -> str:
    """把 SQLite 占位符 SQL 转成 psycopg 格式。

    LIKE 等字面量里的 `%` 先写成 `%%`，再把 `?` 换成 `%s`，
    避免 psycopg 把通配符 `%` 当成非法占位符。
    """
    return sql.replace("%", "%%").replace("?", "%s")


class PgConnection:
    dialect = "postgres"

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._closed = False

    def execute(self, sql: str, params: Any = None) -> PgCursor:
        adapted = adapt_sqlite_sql(sql)
        if params is None:
            cursor = self._inner.execute(adapted)
        else:
            cursor = self._inner.execute(adapted, tuple(params))
        return PgCursor(cursor)

    def executescript(self, sql: str) -> None:
        for statement in sql.split(";"):
            statement = statement.strip()
            if statement:
                self._inner.execute(statement)

    def commit(self) -> None:
        self._inner.commit()

    def close(self) -> None:
        if not self._closed:
            self._inner.close()
            self._closed = True

    def __enter__(self) -> "PgConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self._inner.commit()
        else:
            self._inner.rollback()
        self.close()


def is_postgres(connection: Any) -> bool:
    return getattr(connection, "dialect", "sqlite") == "postgres"


def connect(database_path: Path | None = None) -> Any:
    from app.config import get_settings

    settings = get_settings()
    if settings.database_url:
        import psycopg
        from psycopg.rows import dict_row

        inner = psycopg.connect(settings.database_url, row_factory=dict_row)
        return PgConnection(inner)

    path = database_path or settings.database_file
    ensure_parent_dir(path)
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


_db_initialized = False


def init_db(connection: Any) -> None:
    global _db_initialized
    if _db_initialized:
        return
    if is_postgres(connection):
        connection.executescript(SCHEMA_PG)
    else:
        connection.executescript("DROP TABLE IF EXISTS sync_tasks;")
        connection.executescript("DROP TABLE IF EXISTS browser_news;")
        connection.executescript(SCHEMA)
    ensure_task_columns(connection)
    ensure_news_columns(connection)
    ensure_gui_settings(connection)
    connection.execute("UPDATE news SET task_id = 0 WHERE task_id IS NULL")
    connection.commit()
    _db_initialized = True


def _existing_columns(connection: Any, table: str) -> set[str]:
    if is_postgres(connection):
        rows = connection.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ?
            """,
            (table,),
        ).fetchall()
        return {str(row["column_name"]).lower() for row in rows}
    cursor = connection.execute(f"PRAGMA table_info({table})")
    existing: set[str] = set()
    for row in cursor.fetchall():
        name = row["name"] if isinstance(row, sqlite3.Row) else row[1]
        existing.add(str(name).lower())
    return existing


def ensure_task_columns(connection: Any) -> None:
    existing = _existing_columns(connection, "tasks")
    for column, definition in TASK_DISPLAY_COLUMNS.items():
        if column.lower() not in existing:
            connection.execute(f"ALTER TABLE tasks ADD COLUMN {column} {definition}")


def ensure_news_columns(connection: Any) -> None:
    for table in NEWS_SOURCE_METHOD_COLUMNS:
        existing = _existing_columns(connection, table)
        for column, definition in [
            ("source_method", "TEXT NOT NULL DEFAULT ''"),
            ("task_id", "INTEGER NOT NULL DEFAULT 0"),
            ("title_zh", "TEXT"),
        ]:
            if column.lower() not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def ensure_gui_settings(connection: Any) -> None:
    for key, value in GUI_SETTING_DEFAULTS.items():
        connection.execute(
            """
            INSERT INTO gui_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT (key) DO NOTHING
            """,
            (key, value, cst_now()),
        )
