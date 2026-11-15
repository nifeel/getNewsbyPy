import sqlite3
from datetime import datetime

from app.collectors.history_sync_collector import resumable_old_id


def row_for(*, status: str, target_since: str, current_old_id: str) -> sqlite3.Row:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    cursor = connection.execute(
        "SELECT ? AS status, ? AS target_since, ? AS current_old_id",
        (status, target_since, current_old_id),
    )
    return cursor.fetchone()


def test_resumable_old_id_accepts_unfinished_matching_checkpoint() -> None:
    since = datetime(2026, 1, 1)
    row = row_for(status="sleeping", target_since=since.isoformat(), current_old_id="9590000")

    assert resumable_old_id(row, since) == 9590000


def test_resumable_old_id_ignores_completed_checkpoint() -> None:
    since = datetime(2026, 1, 1)
    row = row_for(status="completed", target_since=since.isoformat(), current_old_id="9590000")

    assert resumable_old_id(row, since) is None


def test_resumable_old_id_ignores_different_since() -> None:
    since = datetime(2026, 1, 1)
    row = row_for(status="sleeping", target_since="2026-02-01T00:00:00", current_old_id="9590000")

    assert resumable_old_id(row, since) is None


def test_resumable_old_id_ignores_invalid_old_id() -> None:
    since = datetime(2026, 1, 1)
    row = row_for(status="sleeping", target_since=since.isoformat(), current_old_id="")

    assert resumable_old_id(row, since) is None
