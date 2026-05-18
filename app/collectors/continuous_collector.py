import asyncio
import os
from contextlib import closing

from loguru import logger

from app.collectors.startup_collector import collect_startup_news
from app.config import get_settings
from app.storage.database import connect, init_db
from app.storage.task_status import TaskStatusRepository, utc_now


TASK_NAME = "continuous_collector"


def update_task_status(status: str, **values: object) -> None:
    settings = get_settings()
    with closing(connect(settings.database_file)) as connection:
        init_db(connection)
        repository = TaskStatusRepository(connection)
        repository.upsert(TASK_NAME, status, **values)


async def run_continuous_collector() -> None:
    settings = get_settings()
    cycle = 0

    logger.info(
        "Starting continuous collector: interval={}s retry={}s",
        settings.collector_interval_seconds,
        settings.collector_retry_seconds,
    )
    update_task_status("running", pid=os.getpid(), last_started_at=utc_now(), last_error="")

    while True:
        cycle += 1
        try:
            update_task_status("collecting", cycle=cycle, pid=os.getpid(), last_started_at=utc_now(), last_error="")
            inserted, skipped, total = await collect_startup_news()
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
