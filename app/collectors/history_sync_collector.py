import argparse
import asyncio
import os
import sqlite3
from contextlib import closing
from datetime import datetime

from loguru import logger

from app.browser.login import has_local_credentials, relogin_with_retries
from app.browser.session import storage_state_exists
from app.collectors.startup_api_client import (
    LoginRequiredError,
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
from app.storage.task_status import TaskStatusRepository, utc_now


TASK_NAME = "history_sync_collector"
LATEST_TASK_NAME = "latest_catchup_collector"
RESUMABLE_STATUSES = {"running", "fetching", "sleeping", "stopped", "error"}


def parse_since(value: str) -> datetime:
    normalized = value.strip()
    if not normalized:
        raise ValueError("since date is required")
    if len(normalized) == 10:
        normalized = f"{normalized}T00:00:00"
    normalized = normalized.replace(" ", "T")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def update_task_status(status: str, *, task_name: str = TASK_NAME, **values: object) -> None:
    settings = get_settings()
    with closing(connect(settings.database_file)) as connection:
        init_db(connection)
        TaskStatusRepository(connection).upsert(task_name, status, **values)


def resumable_old_id(row: sqlite3.Row | None, since: datetime) -> int | None:
    if row is None:
        return None
    if row["status"] not in RESUMABLE_STATUSES:
        return None
    if row["target_since"] != since.isoformat():
        return None

    try:
        old_id = int(row["current_old_id"])
    except (TypeError, ValueError):
        return None
    if old_id <= 0:
        return None
    return old_id


def read_resume_state(since: datetime) -> tuple[int, int, int, int, int] | None:
    settings = get_settings()
    with closing(connect(settings.database_file)) as connection:
        init_db(connection)
        row = TaskStatusRepository(connection).get(TASK_NAME)

    old_id = resumable_old_id(row, since)
    if old_id is None or row is None:
        return None
    return old_id, int(row["pages"]), int(row["inserted"]), int(row["skipped"]), int(row["total"])


def insert_items(items: list[NewsItem]) -> tuple[int, int, int]:
    settings = get_settings()
    with closing(connect(settings.database_file)) as connection:
        init_db(connection)
        repository = NewsRepository(connection)
        inserted, skipped = repository.insert_many(items)
        total = repository.count()
    return inserted, skipped, total


def page_oldest_item(items: list[NewsItem]) -> NewsItem | None:
    dated_items = [item for item in items if item.published_at is not None]
    if dated_items:
        return min(dated_items, key=lambda item: item.published_at or datetime.max)
    if items:
        return min(items, key=lambda item: int(item.external_id) if item.external_id.isdigit() else 0)
    return None


def next_old_id(items: list[NewsItem], current_old_id: int) -> int:
    ids = [int(item.external_id) for item in items if item.external_id.isdigit()]
    if not ids:
        return current_old_id
    return min(ids)


async def fetch_payload_with_relogin(request_url: str) -> dict[str, object]:
    settings = get_settings()
    try:
        return await fetch_startup_payload(request_url, settings.storage_state_file)
    except LoginRequiredError as exc:
        logger.warning("Login state appears expired during history sync: {}", exc)
        await relogin_with_retries(str(exc))
        return await fetch_startup_payload(request_url, settings.storage_state_file)


async def sleep_or_stop(stop_event: asyncio.Event, seconds: int) -> None:
    if seconds <= 0:
        return
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        return


async def run_latest_catchup(
    base_url: str,
    since: datetime,
    interval_seconds: int,
    stop_event: asyncio.Event,
    duplicate_page_limit: int = 2,
    initial_delay_seconds: int = 0,
) -> None:
    pages = 0
    inserted_total = 0
    skipped_total = 0
    database_total = 0

    update_task_status(
        "running",
        task_name=LATEST_TASK_NAME,
        pages=0,
        inserted=0,
        skipped=0,
        total=0,
        target_since=since.isoformat(),
        current_old_id="0",
        current_oldest_at=None,
        message="starting latest catch-up",
        pid=os.getpid(),
        last_started_at=utc_now(),
        last_error="",
    )

    await sleep_or_stop(stop_event, initial_delay_seconds)

    while not stop_event.is_set():
        old_id = 0
        duplicate_pages = 0
        cycle_inserted = 0
        cycle_skipped = 0

        while not stop_event.is_set():
            request_url = startup_url_with_old_id(base_url, old_id)
            update_task_status(
                "fetching",
                task_name=LATEST_TASK_NAME,
                pages=pages,
                cycle=pages,
                current_old_id=str(old_id),
                message=f"catching up latest gap from oldID={old_id}",
                pid=os.getpid(),
            )

            try:
                payload = await fetch_payload_with_relogin(request_url)
            except Exception as exc:
                update_task_status(
                    "error",
                    task_name=LATEST_TASK_NAME,
                    pid=os.getpid(),
                    last_error=str(exc),
                    message="latest catch-up failed; retrying",
                )
                logger.warning("Latest catch-up failed: {}", exc)
                await sleep_or_stop(stop_event, max(interval_seconds, 5))
                break

            items = parse_startup_payload(payload)  # type: ignore[arg-type]
            pages += 1
            if not items:
                update_task_status(
                    "sleeping",
                    task_name=LATEST_TASK_NAME,
                    pages=pages,
                    message="latest catch-up found no items",
                    pid=os.getpid(),
                    last_error="",
                )
                break

            eligible_items = [item for item in items if item.published_at is None or item.published_at >= since]
            inserted, skipped, database_total = insert_items(eligible_items)
            inserted_total += inserted
            skipped_total += skipped
            cycle_inserted += inserted
            cycle_skipped += skipped

            oldest_item = page_oldest_item(items)
            oldest_at = oldest_item.published_at if oldest_item and oldest_item.published_at else None
            next_id = next_old_id(items, old_id)
            all_eligible_duplicates = bool(eligible_items) and inserted == 0 and skipped >= len(eligible_items)
            duplicate_pages = duplicate_pages + 1 if all_eligible_duplicates else 0
            reached_since = oldest_at is not None and oldest_at < since
            message = (
                f"latest_cycle_inserted={cycle_inserted} latest_cycle_skipped={cycle_skipped} "
                f"duplicate_pages={duplicate_pages}/{duplicate_page_limit}"
            )
            update_task_status(
                "sleeping" if duplicate_pages >= duplicate_page_limit or reached_since else "fetching",
                task_name=LATEST_TASK_NAME,
                pages=pages,
                cycle=pages,
                inserted=inserted_total,
                skipped=skipped_total,
                total=database_total,
                current_old_id=str(next_id),
                current_oldest_at=oldest_at.isoformat() if oldest_at else None,
                message=message,
                pid=os.getpid(),
                last_error="",
            )

            if reached_since or duplicate_pages >= duplicate_page_limit or next_id == old_id:
                break
            old_id = next_id

        await sleep_or_stop(stop_event, interval_seconds)

    update_task_status(
        "completed",
        task_name=LATEST_TASK_NAME,
        pages=pages,
        inserted=inserted_total,
        skipped=skipped_total,
        total=database_total,
        pid=os.getpid(),
        message="latest catch-up stopped after history sync finished",
        last_finished_at=utc_now(),
        last_error="",
    )


async def run_history_sync(
    since: datetime,
    interval_seconds: int,
    max_pages: int | None = None,
    *,
    resume: bool = True,
) -> tuple[int, int, int]:
    settings = get_settings()
    if not storage_state_exists(settings.storage_state_file):
        if has_local_credentials():
            logger.warning("Login state file is missing; attempting automatic login")
            await relogin_with_retries("missing login state file")
        else:
            raise FileNotFoundError(
                f"Login state not found: {settings.storage_state_file}. Run `python -m app.browser.login` first."
            )

    base_url = resolve_startup_url(settings.startup_api_url, settings.startup_api_cache_file, settings.network_log_file)
    write_cached_startup_url(settings.startup_api_cache_file, base_url)

    resume_state = read_resume_state(since) if resume else None
    if resume_state:
        old_id, pages, inserted_total, skipped_total, database_total = resume_state
        start_message = f"resuming history sync from oldID={old_id}"
    else:
        old_id = 0
        pages = 0
        inserted_total = 0
        skipped_total = 0
        database_total = 0
        start_message = "starting history sync"
    latest_initial_delay = 0 if resume_state else interval_seconds
    latest_stop_event = asyncio.Event()
    latest_task = asyncio.create_task(
        run_latest_catchup(
            base_url,
            since,
            interval_seconds,
            latest_stop_event,
            initial_delay_seconds=latest_initial_delay,
        )
    )

    update_task_status(
        "running",
        pages=pages,
        inserted=inserted_total,
        skipped=skipped_total,
        total=database_total,
        target_since=since.isoformat(),
        current_old_id=str(old_id),
        current_oldest_at=None,
        message=start_message,
        pid=os.getpid(),
        last_started_at=utc_now(),
        last_error="",
    )

    try:
        while True:
            if max_pages is not None and pages >= max_pages:
                message = f"stopped after max_pages={max_pages}"
                update_task_status("completed", message=message, last_finished_at=utc_now())
                logger.info(message)
                break

            request_url = startup_url_with_old_id(base_url, old_id)
            update_task_status(
                "fetching",
                pages=pages,
                current_old_id=str(old_id),
                message=f"fetching history page {pages + 1}",
                pid=os.getpid(),
            )
            payload = await fetch_payload_with_relogin(request_url)
            items = parse_startup_payload(payload)  # type: ignore[arg-type]
            pages += 1

            if not items:
                update_task_status("completed", pages=pages, message="no more items", last_finished_at=utc_now())
                break

            eligible_items = [
                item for item in items if item.published_at is None or item.published_at >= since
            ]
            inserted, skipped, database_total = insert_items(eligible_items)
            inserted_total += inserted
            skipped_total += skipped

            oldest_item = page_oldest_item(items)
            oldest_at = oldest_item.published_at if oldest_item and oldest_item.published_at else None
            next_id = next_old_id(items, old_id)
            reached_since = oldest_at is not None and oldest_at < since
            message = (
                f"history_page={pages} page_items={len(items)} eligible={len(eligible_items)} "
                f"inserted_total={inserted_total} skipped_total={skipped_total}"
            )
            update_task_status(
                "sleeping" if not reached_since else "completed",
                pages=pages,
                cycle=pages,
                inserted=inserted_total,
                skipped=skipped_total,
                total=database_total,
                current_old_id=str(next_id),
                current_oldest_at=oldest_at.isoformat() if oldest_at else None,
                message=message,
                pid=os.getpid(),
                last_finished_at=utc_now() if reached_since else None,
                last_error="",
            )
            logger.info(message)
            print(message, flush=True)

            if reached_since:
                break
            if next_id == old_id:
                update_task_status("completed", message="oldID did not advance", last_finished_at=utc_now())
                break

            old_id = next_id
            await sleep_or_stop(latest_stop_event, interval_seconds)
    finally:
        latest_stop_event.set()
        await latest_task

    return inserted_total, skipped_total, database_total


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Slowly backfill FinancialJuice history from a date to now.")
    parser.add_argument("--since", required=True, help="Start date, e.g. 2026-05-17 or 2026-05-17T09:00:00")
    parser.add_argument("--interval-seconds", type=int, default=None, help="Delay between pages. Defaults to FJ_HISTORY_SYNC_INTERVAL_SECONDS.")
    parser.add_argument("--max-pages", type=int, default=None, help="Optional safety limit for testing.")
    parser.add_argument("--restart", action="store_true", help="Ignore a saved unfinished checkpoint and start from the latest page.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = get_settings()
    interval_seconds = args.interval_seconds
    if interval_seconds is None:
        interval_seconds = settings.history_sync_interval_seconds
    if interval_seconds < 0:
        raise ValueError("--interval-seconds must be >= 0")

    try:
        since = parse_since(args.since)
        inserted, skipped, total = asyncio.run(
            run_history_sync(since, interval_seconds, args.max_pages, resume=not args.restart)
        )
    except KeyboardInterrupt:
        update_task_status("stopped", pid=os.getpid(), last_finished_at=utc_now(), message="stopped by user")
        logger.info("History sync stopped by user")
        return
    except Exception as exc:
        update_task_status("error", pid=os.getpid(), last_finished_at=utc_now(), last_error=str(exc), message="history sync failed")
        raise

    print(f"Inserted: {inserted}")
    print(f"Skipped duplicates: {skipped}")
    print(f"Total stored: {total}")


if __name__ == "__main__":
    main()
