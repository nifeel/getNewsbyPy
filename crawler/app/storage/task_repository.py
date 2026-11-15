import sqlite3

from app.storage.task_status import cst_now


class TaskRepository:
    TASK_TYPES = ('realtime_browser', 'history_api', 'login_check')
    STATUSES = ('pending', 'running', 'paused', 'completed', 'error', 'stopped')

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def create(
        self,
        task_type: str,
        start_news_id: int | None = None,
        end_news_id: int | None = None,
        end_time: str | None = None,
    ) -> int:
        now = cst_now()
        cursor = self.connection.execute(
            """
            INSERT INTO tasks (
                task_type, start_news_id, end_news_id, end_time,
                status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'pending', ?, ?)
            RETURNING id
            """,
            (task_type, start_news_id, end_news_id, end_time, now, now),
        )
        row = cursor.fetchone()
        self.connection.commit()
        return int(row["id"])

    def update(
        self,
        task_id: int,
        status: str,
        *,
        current_news_id: int | None = None,
        pid: int | None = None,
        last_error: str | None = None,
        cycle: int | None = None,
        inserted: int | None = None,
        skipped: int | None = None,
        total: int | None = None,
        pages: int | None = None,
        current_old_id: str | None = None,
        current_oldest_at: str | None = None,
        message: str | None = None,
        last_started_at: str | None = None,
        last_finished_at: str | None = None,
    ) -> None:
        fields = ["status = ?", "updated_at = ?"]
        values: list = [status, cst_now()]

        if current_news_id is not None:
            fields.append("current_news_id = ?")
            values.append(current_news_id)
        if pid is not None:
            fields.append("pid = ?")
            values.append(pid)
        if last_error is not None:
            fields.append("last_error = ?")
            values.append(last_error)
        if cycle is not None:
            fields.append("cycle = ?")
            values.append(cycle)
        if inserted is not None:
            fields.append("inserted = ?")
            values.append(inserted)
        if skipped is not None:
            fields.append("skipped = ?")
            values.append(skipped)
        if total is not None:
            fields.append("total = ?")
            values.append(total)
        if pages is not None:
            fields.append("pages = ?")
            values.append(pages)
        if current_old_id is not None:
            fields.append("current_old_id = ?")
            values.append(current_old_id)
        if current_oldest_at is not None:
            fields.append("current_oldest_at = ?")
            values.append(current_oldest_at)
        if message is not None:
            fields.append("message = ?")
            values.append(message)
        if last_started_at is not None:
            fields.append("last_started_at = ?")
            values.append(last_started_at)
        if last_finished_at is not None:
            fields.append("last_finished_at = ?")
            values.append(last_finished_at)

        values.append(task_id)
        self.connection.execute(
            f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        self.connection.commit()

    def get(self, task_id: int) -> sqlite3.Row | None:
        cursor = self.connection.execute(
            "SELECT * FROM tasks WHERE id = ?",
            (task_id,),
        )
        return cursor.fetchone()

    def get_active(self, task_type: str) -> sqlite3.Row | None:
        cursor = self.connection.execute(
            """
            SELECT * FROM tasks
            WHERE task_type = ? AND status IN ('pending', 'running', 'paused')
            LIMIT 1
            """,
            (task_type,),
        )
        return cursor.fetchone()

    def has_active(self, task_type: str) -> bool:
        return self.get_active(task_type) is not None

    def get_next_history_task(self) -> sqlite3.Row | None:
        """返回最新的未完成历史任务（包括补缺和长历史扫描）。"""
        cursor = self.connection.execute(
            """
            SELECT * FROM tasks
            WHERE task_type = 'history_api'
              AND status NOT IN ('completed', 'stopped')
            ORDER BY id DESC
            LIMIT 1
            """,
        )
        return cursor.fetchone()

    def get_all_incomplete_history(self) -> list[sqlite3.Row]:
        cursor = self.connection.execute(
            """
            SELECT * FROM tasks
            WHERE task_type = 'history_api'
              AND status NOT IN ('completed', 'error')
            ORDER BY created_at DESC
            """,
        )
        return list(cursor.fetchall())

    def get_next_gap_task(self) -> sqlite3.Row | None:
        """返回最高优先级的待处理补缺任务（end_news_id 已设置，end_time 为 NULL）。"""
        cursor = self.connection.execute(
            """
            SELECT * FROM tasks
            WHERE task_type = 'history_api'
              AND end_news_id IS NOT NULL
              AND end_time IS NULL
              AND status NOT IN ('completed', 'stopped')
            ORDER BY start_news_id DESC
            LIMIT 1
            """,
        )
        return cursor.fetchone()

    def has_active_gap_task_covering(self, start_news_id: int, end_news_id: int) -> bool:
        cursor = self.connection.execute(
            """
            SELECT 1 FROM tasks
            WHERE task_type = 'history_api'
              AND status IN ('pending', 'running', 'paused')
              AND end_news_id IS NOT NULL
              AND start_news_id >= ?
              AND end_news_id <= ?
            LIMIT 1
            """,
            (start_news_id, end_news_id),
        )
        return cursor.fetchone() is not None

    def has_active_history_task(self) -> bool:
        cursor = self.connection.execute(
            """
            SELECT 1 FROM tasks
            WHERE task_type = 'history_api'
              AND end_time IS NOT NULL
              AND status IN ('pending', 'running', 'paused')
            LIMIT 1
            """,
        )
        return cursor.fetchone() is not None

    def list_all(self) -> list[sqlite3.Row]:
        cursor = self.connection.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC",
        )
        return list(cursor.fetchall())
