import sqlite3
from pathlib import Path

from app.storage.task_status import cst_now


def write_task_log(
    database_path: Path,
    task_id: int,
    task_type: str,
    level: str,
    message: str,
) -> None:
    """向 task_logs 表中插入一条日志记录。

    该表由 ``app.storage.database.init_db()`` 创建。
    """
    import sqlite3

    from app.storage.database import connect, init_db

    with connect(database_path) as conn:
        init_db(conn)
        conn.execute(
            """
            INSERT INTO task_logs (task_id, task_type, level, message, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (task_id, task_type, level, message, cst_now()),
        )
        conn.commit()
