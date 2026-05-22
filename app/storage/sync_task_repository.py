import sqlite3

from app.storage.task_status import cst_now


class SyncTaskRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def create(
        self,
        start_news_id: int,
        end_news_id: int | None = None,
        target_since: str | None = None,
    ) -> int:
        now = cst_now()
        cursor = self.connection.execute(
            """
            INSERT INTO sync_tasks
                (start_news_id, end_news_id, target_since, current_news_id, status, inserted, skipped, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', 0, 0, ?, ?)
            """,
            (start_news_id, end_news_id, target_since, start_news_id, now, now),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def has_start(self, start_news_id: int) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM sync_tasks WHERE start_news_id = ?",
            (start_news_id,),
        ).fetchone()
        return row is not None

    def get_next_runnable(self) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT * FROM sync_tasks
            WHERE status IN ('pending', 'running')
            ORDER BY start_news_id DESC
            LIMIT 1
            """
        ).fetchone()

    def update_checkpoint(
        self,
        task_id: int,
        current_news_id: int,
        inserted: int,
        skipped: int,
    ) -> None:
        self.connection.execute(
            """
            UPDATE sync_tasks
            SET current_news_id = ?, inserted = ?, skipped = ?, status = 'running', updated_at = ?
            WHERE id = ?
            """,
            (current_news_id, inserted, skipped, cst_now(), task_id),
        )
        self.connection.commit()

    def mark_completed(self, task_id: int, inserted: int, skipped: int) -> None:
        self.connection.execute(
            """
            UPDATE sync_tasks
            SET status = 'completed', inserted = ?, skipped = ?, updated_at = ?
            WHERE id = ?
            """,
            (inserted, skipped, cst_now(), task_id),
        )
        self.connection.commit()

    def list_all(self) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                "SELECT * FROM sync_tasks ORDER BY start_news_id DESC"
            ).fetchall()
        )
