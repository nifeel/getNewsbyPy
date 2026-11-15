"""把 SQLite news.db 全表导入 PostgreSQL。导完后重置 IDENTITY 序列。"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

TABLES = ("news", "login_events", "gui_settings", "tasks", "task_logs", "task_status")
IDENTITY_TABLES = ("news", "login_events", "tasks", "task_logs")


def _columns(src: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in src.execute(f"PRAGMA table_info({table})").fetchall()]


def _reset_identity(dest, table: str) -> None:
    row = dest.execute(f"SELECT COALESCE(MAX(id), 0) AS max_id FROM {table}").fetchone()
    max_id = int(row["max_id"] if row["max_id"] is not None else row[0])
    if max_id < 1:
        return
    dest.execute(
        "SELECT setval(pg_get_serial_sequence(?, 'id'), ?, true)",
        (table, max_id),
    )


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: migrate_sqlite_to_pg.py <sqlite_path> <postgres_url>", file=sys.stderr)
        sys.exit(2)

    sqlite_path = Path(sys.argv[1])
    pg_url = sys.argv[2]
    os.environ["FJ_DATABASE_URL"] = pg_url

    from app.config import get_settings
    from app.storage.database import connect, init_db

    get_settings.cache_clear()

    src = sqlite3.connect(str(sqlite_path))
    src.row_factory = sqlite3.Row
    dest = connect()
    init_db(dest)

    for table in TABLES:
        dest.execute(f"DELETE FROM {table}")
    dest.commit()

    for table in TABLES:
        cols = _columns(src, table)
        if not cols:
            print(f"skip missing sqlite table {table}")
            continue
        col_sql = ", ".join(cols)
        placeholders = ", ".join("?" for _ in cols)
        rows = list(src.execute(f"SELECT {col_sql} FROM {table}"))
        insert_sql = f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})"
        for row in rows:
            dest.execute(insert_sql, tuple(row))
        dest.commit()
        print(f"{table}: copied {len(rows)}")

    for table in IDENTITY_TABLES:
        _reset_identity(dest, table)
    dest.commit()

    sqlite_news = src.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    pg_news = dest.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    sqlite_max = src.execute("SELECT MAX(NewsID) FROM news").fetchone()[0]
    pg_max = dest.execute("SELECT MAX(NewsID) FROM news").fetchone()[0]
    print(f"news sqlite={sqlite_news} pg={pg_news} max_id sqlite={sqlite_max} pg={pg_max}")
    if sqlite_news != pg_news or sqlite_max != pg_max:
        raise SystemExit("news count or max NewsID mismatch")

    dest.close()
    src.close()
    print("MIGRATE_OK")


if __name__ == "__main__":
    main()
