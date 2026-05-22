import asyncio
import os
import random
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Any

from loguru import logger

from app.collectors.startup_api_client import (
    LoginRequiredError,
    StartupApiError,
    fetch_startup_payload,
    resolve_startup_url,
    startup_url_with_old_id,
    write_cached_startup_url,
)
from app.config import get_settings
from app.models import NewsItem
from app.parsers.financialjuice import parse_startup_payload
from app.storage.database import connect, init_db
from app.storage.repository import NewsRepository
from app.storage.sync_task_repository import SyncTaskRepository
from app.storage.task_status import TaskStatusRepository, utc_now


TASK_NAME = "sync_task_runner"
MAX_CONSECUTIVE_ERRORS = 3
IDLE_POLL_SECONDS = 60


def _update_status(status: str, **values: object) -> None:
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        TaskStatusRepository(conn).upsert(TASK_NAME, status, **values)


async def _sleep(stop_event: asyncio.Event, seconds: float) -> bool:
    """Sleep for approximately `seconds`. Returns True if stop_event fired."""
    jitter = min(10, max(0, int(seconds * 0.1)))
    actual = max(1, seconds + random.randint(-jitter, jitter))
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=actual)
        return True
    except asyncio.TimeoutError:
        return False


async def _resolve_url() -> str:
    settings = get_settings()
    for attempt in range(12):
        try:
            url = resolve_startup_url(
                settings.startup_api_url,
                settings.startup_api_cache_file,
                settings.network_log_file,
            )
            write_cached_startup_url(settings.startup_api_cache_file, url)
            return url
        except StartupApiError:
            if attempt == 11:
                raise
            logger.warning("Startup URL not yet available, retrying in 10s ({}/12)", attempt + 1)
            await asyncio.sleep(10)
    raise StartupApiError("URL resolution failed")


async def _fetch(url: str, stop_event: asyncio.Event) -> dict[str, Any]:
    settings = get_settings()
    for attempt in range(3):
        try:
            return await fetch_startup_payload(url, settings.storage_state_file)
        except LoginRequiredError as exc:
            if attempt >= 2:
                raise
            wait = settings.login_check_interval_seconds + 10
            logger.warning("Login required, waiting {}s (attempt {}/3): {}", wait, attempt + 1, exc)
            await _sleep(stop_event, wait)
    raise LoginRequiredError("Login failed after retries")


def _page_oldest_item(items: list[NewsItem]) -> NewsItem | None:
    dated = [item for item in items if item.published_at is not None]
    if dated:
        return min(dated, key=lambda item: item.published_at or datetime.max)
    if items:
        return min(items, key=lambda item: int(item.external_id) if item.external_id.isdigit() else 0)
    return None


def _next_old_id(items: list[NewsItem], fallback: int) -> int:
    ids = [int(item.external_id) for item in items if item.external_id.isdigit()]
    return min(ids) if ids else fallback


def _find_jump(current_news_id: int, since_iso: str) -> int | None:
    if current_news_id <= 0:
        return None
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        return NewsRepository(conn).find_block_bottom(current_news_id - 1, since_iso)


def _insert_items(items: list[NewsItem]) -> tuple[int, int]:
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        inserted, skipped, new_ids, min_skipped_id = NewsRepository(conn).insert_many(items)
    if new_ids:
        print(f"new NewsIDs: {new_ids}", flush=True)
    if skipped > 0 and min_skipped_id is not None:
        print(f"skipped={skipped} min_skipped_id={min_skipped_id}", flush=True)
    return inserted, skipped


def _save_checkpoint(task_id: int, current_news_id: int, inserted: int, skipped: int) -> None:
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        SyncTaskRepository(conn).update_checkpoint(task_id, current_news_id, inserted, skipped)


def _finish_task(task_id: int, inserted: int, skipped: int) -> None:
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        SyncTaskRepository(conn).mark_completed(task_id, inserted, skipped)


async def run_task(
    task_row: sqlite3.Row,
    base_url: str,
    stop_event: asyncio.Event,
    interval_seconds: int,
) -> tuple[bool, str]:
    """
    Execute one sync task page by page.
    Returns (completed, updated_base_url).
    """
    task_id: int = task_row["id"]
    end_news_id: int | None = task_row["end_news_id"]
    target_since_str: str | None = task_row["target_since"]
    current_news_id: int = task_row["current_news_id"]
    inserted_total: int = task_row["inserted"]
    skipped_total: int = task_row["skipped"]

    target_since: datetime | None = None
    if target_since_str:
        try:
            target_since = datetime.fromisoformat(target_since_str)
        except ValueError:
            logger.error("Invalid target_since '{}' for task id={}", target_since_str, task_id)

    since_iso = target_since.isoformat() if target_since else "1900-01-01T00:00:00"
    task_type = "history" if target_since else "gap"
    pages = 0
    consecutive_errors = 0

    logger.info(
        "Starting {} task id={} start={} end={} since={} resume_at={}",
        task_type, task_id, task_row["start_news_id"], end_news_id, target_since_str, current_news_id,
    )
    _update_status(
        "running",
        pages=pages,
        inserted=inserted_total,
        skipped=skipped_total,
        target_since=target_since_str,
        current_old_id=str(current_news_id),
        message=f"running {task_type} task id={task_id}",
        pid=os.getpid(),
        last_started_at=utc_now(),
        last_error="",
    )

    while not stop_event.is_set():
        if end_news_id is not None and current_news_id <= end_news_id:
            logger.info("Gap task id={} complete: current={} reached end={}", task_id, current_news_id, end_news_id)
            _finish_task(task_id, inserted_total, skipped_total)
            return True, base_url

        jump = _find_jump(current_news_id, since_iso)
        if jump is not None and jump < current_news_id:
            logger.info("task id={}: jump {} → {}", task_id, current_news_id, jump)
            current_news_id = jump
            _save_checkpoint(task_id, current_news_id, inserted_total, skipped_total)
            continue

        request_url = startup_url_with_old_id(base_url, current_news_id)
        _update_status(
            "fetching",
            pages=pages,
            current_old_id=str(current_news_id),
            message=f"{task_type} task id={task_id} page={pages + 1} old_id={current_news_id}",
            pid=os.getpid(),
        )

        try:
            payload = await _fetch(request_url, stop_event)
            consecutive_errors = 0
        except LoginRequiredError as exc:
            logger.error("task id={}: login required: {}", task_id, exc)
            _update_status("error", pid=os.getpid(), last_error=str(exc), message="login required, waiting")
            await _sleep(stop_event, get_settings().login_check_interval_seconds + 10)
            continue
        except StartupApiError as exc:
            consecutive_errors += 1
            logger.warning("task id={}: fetch error ({}/{}): {}", task_id, consecutive_errors, MAX_CONSECUTIVE_ERRORS, exc)
            _update_status("error", pid=os.getpid(), last_error=str(exc), message="fetch error, retrying")
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                get_settings().startup_api_cache_file.unlink(missing_ok=True)
                consecutive_errors = 0
                try:
                    base_url = await _resolve_url()
                    logger.info("task id={}: URL refreshed", task_id)
                except StartupApiError:
                    logger.error("task id={}: URL refresh failed", task_id)
            await _sleep(stop_event, max(interval_seconds, 30))
            continue

        items = parse_startup_payload(payload)  # type: ignore[arg-type]
        pages += 1

        if not items:
            logger.info("task id={} complete: no more items at old_id={}", task_id, current_news_id)
            _finish_task(task_id, inserted_total, skipped_total)
            return True, base_url

        eligible = items
        if target_since is not None:
            eligible = [i for i in items if i.published_at is None or i.published_at >= target_since]

        inserted, skipped = _insert_items(eligible)
        inserted_total += inserted
        skipped_total += skipped

        oldest_item = _page_oldest_item(items)
        oldest_at = oldest_item.published_at if oldest_item else None
        next_id = _next_old_id(items, current_news_id)

        _save_checkpoint(task_id, next_id, inserted_total, skipped_total)
        _update_status(
            "fetching",
            pages=pages,
            inserted=inserted_total,
            skipped=skipped_total,
            current_old_id=str(next_id),
            current_oldest_at=oldest_at.isoformat() if oldest_at else None,
            message=(
                f"{task_type} task id={task_id} page={pages} "
                f"inserted={inserted_total} skipped={skipped_total} current={next_id}"
            ),
            pid=os.getpid(),
            last_error="",
        )
        logger.info(
            "{} task id={} page={} inserted={} skipped={} current={}",
            task_type, task_id, pages, inserted_total, skipped_total, next_id,
        )
        print(
            f"sync_task id={task_id} page={pages} inserted={inserted_total} skipped={skipped_total} current={next_id}",
            flush=True,
        )

        reached_since = target_since is not None and oldest_at is not None and oldest_at < target_since
        if reached_since:
            logger.info("History task id={} complete: reached target_since={}", task_id, target_since_str)
            _finish_task(task_id, inserted_total, skipped_total)
            return True, base_url

        if next_id >= current_news_id:
            logger.warning("task id={} stalled at old_id={}, marking complete", task_id, next_id)
            _finish_task(task_id, inserted_total, skipped_total)
            return True, base_url

        current_news_id = next_id

        if stop_event.is_set():
            break

        await _sleep(stop_event, interval_seconds)

    return False, base_url


async def run_sync_runner() -> None:
    settings = get_settings()
    stop_event = asyncio.Event()
    base_url: str | None = None

    _update_status("running", pid=os.getpid(), last_started_at=utc_now(), last_error="", message="starting")
    logger.info("Sync task runner started")

    try:
        while not stop_event.is_set():
            with closing(connect(settings.database_file)) as conn:
                init_db(conn)
                task_row = SyncTaskRepository(conn).get_next_runnable()

            if task_row is None:
                _update_status("idle", pid=os.getpid(), message="no pending tasks")
                await _sleep(stop_event, IDLE_POLL_SECONDS)
                continue

            if base_url is None:
                try:
                    base_url = await _resolve_url()
                except StartupApiError as exc:
                    logger.error("Cannot resolve startup URL: {}", exc)
                    _update_status("error", pid=os.getpid(), last_error=str(exc), message="URL resolution failed")
                    await _sleep(stop_event, 60)
                    continue

            completed, base_url = await run_task(
                task_row,
                base_url,
                stop_event,
                settings.history_sync_interval_seconds,
            )
            if not completed:
                break

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _update_status("error", pid=os.getpid(), last_finished_at=utc_now(), last_error=str(exc))
        logger.exception("Sync runner crashed: {}", exc)
        raise
    finally:
        _update_status("stopped", pid=os.getpid(), last_finished_at=utc_now())


def main() -> None:
    from pathlib import Path as _Path
    _Path("data/logs").mkdir(parents=True, exist_ok=True)
    logger.add("data/logs/sync_task_runner.log", rotation="10 MB", retention=5, encoding="utf-8", level="DEBUG")

    try:
        asyncio.run(run_sync_runner())
    except KeyboardInterrupt:
        logger.info("Sync task runner stopped by user")
        print("Sync task runner stopped.", flush=True)


if __name__ == "__main__":
    main()
