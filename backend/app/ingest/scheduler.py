import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.db import SessionLocal
from app.ingest.events import sync_cn_events
from app.ingest.service import ingest_all
from app.llm_settings import load_llm_config
from app.timeutil import BIZ_ZONE

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone=str(BIZ_ZONE))


async def job_ingest_news() -> None:
    session = SessionLocal()
    try:
        await ingest_all(session)
    finally:
        session.close()


def job_sync_events() -> None:
    session = SessionLocal()
    try:
        sync_cn_events(session)
    finally:
        session.close()


async def job_generate_daily_summary() -> None:
    session = SessionLocal()
    try:
        cfg = load_llm_config(session)
        if not (cfg.enabled and cfg.auto_generate):
            return
        from app.summary_service import generate_daily_summary

        await generate_daily_summary(session=session)
    except Exception as exc:
        logger.exception("job_generate_daily_summary failed: %s", exc)
    finally:
        session.close()


def start_scheduler() -> None:
    if scheduler.running:
        return
    scheduler.add_job(
        job_ingest_news,
        "interval",
        minutes=settings.ingest_interval_minutes,
        next_run_time=datetime.now(BIZ_ZONE),
        id="ingest_cn_news",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    scheduler.add_job(
        job_sync_events,
        "cron",
        hour=settings.event_sync_hour,
        minute=0,
        id="sync_cn_events",
        replace_existing=True,
    )

    # 每日自动生成前一天报告
    session = SessionLocal()
    try:
        llm_cfg = load_llm_config(session)
        hour = llm_cfg.auto_generate_hour
        minute = llm_cfg.auto_generate_minute
    finally:
        session.close()

    scheduler.add_job(
        job_generate_daily_summary,
        "cron",
        hour=hour,
        minute=minute,
        id="generate_daily_summary",
        replace_existing=True,
        misfire_grace_time=600,
    )
    scheduler.start()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
