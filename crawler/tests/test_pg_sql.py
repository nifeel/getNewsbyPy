from app.storage.database import adapt_sqlite_sql


def test_adapt_sqlite_sql_escapes_like_wildcard() -> None:
    sql = (
        "SELECT 1 FROM tasks WHERE end_time = ? "
        "AND message LIKE 'completed: reached end_time%'"
    )
    adapted = adapt_sqlite_sql(sql)
    assert adapted == (
        "SELECT 1 FROM tasks WHERE end_time = %s "
        "AND message LIKE 'completed: reached end_time%%'"
    )


def test_adapt_sqlite_sql_plain_placeholder() -> None:
    assert adapt_sqlite_sql("SELECT * FROM news WHERE NewsID = ?") == (
        "SELECT * FROM news WHERE NewsID = %s"
    )
