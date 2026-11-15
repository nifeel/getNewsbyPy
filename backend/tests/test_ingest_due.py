from datetime import datetime, timedelta, timezone

from app.ingest.service import is_due


def test_is_due_force_always_true() -> None:
    now = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)
    last = now - timedelta(minutes=1)
    assert is_due(poll_interval_minutes=10, last_fetch_at=last, now=now, force=True)


def test_is_due_respects_interval() -> None:
    now = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)
    last = now - timedelta(minutes=9)
    assert not is_due(poll_interval_minutes=10, last_fetch_at=last, now=now, force=False)
    last = now - timedelta(minutes=10)
    assert is_due(poll_interval_minutes=10, last_fetch_at=last, now=now, force=False)


def test_is_due_when_never_fetched() -> None:
    now = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)
    assert is_due(poll_interval_minutes=1440, last_fetch_at=None, now=now, force=False)
