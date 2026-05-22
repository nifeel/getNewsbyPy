import asyncio
import os
from contextlib import closing

from loguru import logger

from app.collectors.startup_collector import collect_startup_news
from app.config import get_settings
from app.storage.database import connect, init_db
from app.storage.repository import NewsRepository
from app.storage.sync_task_repository import SyncTaskRepository
from app.storage.task_status import TaskStatusRepository, utc_now


TASK_NAME = "continuous_collector"


def update_task_status(status: str, **values: object) -> None:
    settings = get_settings()
    with closing(connect(settings.database_file)) as connection:
        init_db(connection)
        repository = TaskStatusRepository(connection)
        repository.upsert(TASK_NAME, status, **values)


def _create_initial_sync_tasks(db_max: int, batch_min: int) -> None:
    if batch_min <= 0:
        return
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        repo = SyncTaskRepository(conn)

        if repo.has_start(batch_min):
            logger.info("Sync task for start={} already exists, skipping creation", batch_min)
            return

        if db_max == 0:
            row = conn.execute("SELECT value FROM gui_settings WHERE key = 'history_since'").fetchone()
            history_since = row["value"] if row else ""
            if not history_since:
                logger.info("DB is empty but history_since not configured; no history task created")
                return
            task_id = repo.create(batch_min, target_since=history_since)
            logger.info("Created history task id={} start={} since={}", task_id, batch_min, history_since)
            print(f"Created history task: start={batch_min}, since={history_since}", flush=True)

        elif batch_min > db_max + 1:
            task_id = repo.create(batch_min, end_news_id=db_max)
            logger.info("Created gap task id={} start={} end={}", task_id, batch_min, db_max)
            print(f"Created gap task: start={batch_min}, end={db_max}", flush=True)

        else:
            logger.info("No gap detected: batch_min={} db_max={}", batch_min, db_max)


async def run_continuous_collector() -> None:
    settings = get_settings()
    cycle = 0
    first_cycle_done = False
    db_max_before_first = 0

    logger.info(
        "Starting continuous collector: interval={}s retry={}s",
        settings.collector_interval_seconds,
        settings.collector_retry_seconds,
    )
    update_task_status("running", pid=os.getpid(), last_started_at=utc_now(), last_error="")

    while True:
        cycle += 1
        try:
            if not first_cycle_done:
                with closing(connect(settings.database_file)) as conn:
                    init_db(conn)
                    db_max_before_first = NewsRepository(conn).max_news_id()

            update_task_status("collecting", cycle=cycle, pid=os.getpid(), last_started_at=utc_now(), last_error="")
            inserted, skipped, total, batch_min = await collect_startup_news()

            if not first_cycle_done:
                first_cycle_done = True
                _create_initial_sync_tasks(db_max_before_first, batch_min)

            update_task_status(
                "sleeping",
                cycle=cycle,
                inserted=inserted,
                skipped=skipped,
                total=total,
                pid=os.getpid(),
                last_finished_at=utc_now(),
                last_error="",
            )
            logger.info(
                "Cycle {} complete: inserted={} skipped={} total={}",
                cycle,
                inserted,
                skipped,
                total,
            )
            print(f"Cycle {cycle}: inserted={inserted}, skipped={skipped}, total={total}", flush=True)
            await asyncio.sleep(settings.collector_interval_seconds)
        except asyncio.CancelledError:
            update_task_status("stopped", cycle=cycle, pid=os.getpid(), last_finished_at=utc_now())
            raise
        except Exception as exc:
            update_task_status("error", cycle=cycle, pid=os.getpid(), last_finished_at=utc_now(), last_error=str(exc))
            logger.exception("Cycle {} failed: {}", cycle, exc)
            print(f"Cycle {cycle} failed: {exc}", flush=True)
            await asyncio.sleep(settings.collector_retry_seconds)


def main() -> None:
    try:
        asyncio.run(run_continuous_collector())
    except KeyboardInterrupt:
        update_task_status("stopped", pid=os.getpid(), last_finished_at=utc_now())
        logger.info("Continuous collector stopped by user")
        print("Continuous collector stopped.", flush=True)


if __name__ == "__main__":
    main()
