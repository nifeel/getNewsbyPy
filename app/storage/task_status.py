import os
import sqlite3
from datetime import datetime, timedelta


def utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def cst_now() -> str:
    return (datetime.utcnow() + timedelta(hours=8)).isoformat(timespec="seconds")


class TaskStatusRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def upsert(
        self,
        task_name: str,
        status: str,
        *,
        cycle: int | None = None,
        inserted: int | None = None,
        skipped: int | None = None,
        total: int | None = None,
        pages: int | None = None,
        target_since: str | None = None,
        current_old_id: str | None = None,
        current_oldest_at: str | None = None,
        message: str | None = None,
        pid: int | None = None,
        last_started_at: str | None = None,
        last_finished_at: str | None = None,
        last_error: str | None = None,
    ) -> None:
        current = self.get(task_name)
        values = {
            "cycle": cycle if cycle is not None else (current["cycle"] if current else 0),
            "inserted": inserted if inserted is not None else (current["inserted"] if current else 0),
            "skipped": skipped if skipped is not None else (current["skipped"] if current else 0),
            "total": total if total is not None else (current["total"] if current else 0),
            "pages": pages if pages is not None else (current["pages"] if current else 0),
            "target_since": target_since if target_since is not None else (current["target_since"] if current else None),
            "current_old_id": current_old_id if current_old_id is not None else (current["current_old_id"] if current else ""),
            "current_oldest_at": current_oldest_at if current_oldest_at is not None else (current["current_oldest_at"] if current else None),
            "message": message if message is not None else (current["message"] if current else ""),
            "pid": pid if pid is not None else (current["pid"] if current else os.getpid()),
            "last_started_at": last_started_at if last_started_at is not None else (current["last_started_at"] if current else None),
            "last_finished_at": last_finished_at if last_finished_at is not None else (current["last_finished_at"] if current else None),
            "last_error": last_error if last_error is not None else (current["last_error"] if current else ""),
        }

        self.connection.execute(
            """
            INSERT INTO task_status (
                task_name, status, cycle, inserted, skipped, total, pages,
                target_since, current_old_id, current_oldest_at, message,
                pid, last_started_at, last_finished_at, last_error, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_name) DO UPDATE SET
                status = excluded.status,
                cycle = excluded.cycle,
                inserted = excluded.inserted,
                skipped = excluded.skipped,
                total = excluded.total,
                pages = excluded.pages,
                target_since = excluded.target_since,
                current_old_id = excluded.current_old_id,
                current_oldest_at = excluded.current_oldest_at,
                message = excluded.message,
                pid = excluded.pid,
                last_started_at = excluded.last_started_at,
                last_finished_at = excluded.last_finished_at,
                last_error = excluded.last_error,
                updated_at = excluded.updated_at
            """,
            (
                task_name,
                status,
                values["cycle"],
                values["inserted"],
                values["skipped"],
                values["total"],
                values["pages"],
                values["target_since"],
                values["current_old_id"],
                values["current_oldest_at"],
                values["message"],
                values["pid"],
                values["last_started_at"],
                values["last_finished_at"],
                values["last_error"],
                utc_now(),
            ),
        )
        self.connection.commit()

    def get(self, task_name: str) -> sqlite3.Row | None:
        cursor = self.connection.execute(
            "SELECT * FROM task_status WHERE task_name = ?",
            (task_name,),
        )
        return cursor.fetchone()

    def list_all(self) -> list[sqlite3.Row]:
        cursor = self.connection.execute("SELECT * FROM task_status ORDER BY updated_at DESC")
        return list(cursor.fetchall())
