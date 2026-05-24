"""历史 API 同步服务。

从 tasks 表中读取 `history_api` 任务，通过 GetPreviousNews API 获取历史新闻
页面，从 `current_news_id` 向前回溯，直到最早条目的 DatePublished 早于
`end_time`（或 API 不再返回数据）。

每页进度都会做检查点记录，以便任务中断后可恢复。同时更新 task_status 表
以保持 GUI 兼容性。

用法:
    python -m app.services.history_api <task_id>
"""

import asyncio
import os
import random
import sys
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
from app.collectors.startup_collector import _batch_min_id
from app.storage.database import connect, init_db, SOURCE_METHOD_HISTORY
from app.storage.repository import NewsRepository
from app.storage.task_repository import TaskRepository
from app.storage.task_status import cst_now
from app.services.login_utils import read_login_status, wait_for_login


async def _resolve_url() -> str:
    settings = get_settings()
    for attempt in range(URL_RESOLVE_MAX_ATTEMPTS):
        try:
            url = resolve_startup_url(
                settings.startup_api_url,
                settings.startup_api_cache_file,
                settings.network_log_file,
            )
            write_cached_startup_url(settings.startup_api_cache_file, url)
            return url
        except StartupApiError:
            if attempt == URL_RESOLVE_MAX_ATTEMPTS - 1:
                raise
            logger.warning("history_api: startup URL not available, retrying in 10s ({}/{})", attempt + 1, URL_RESOLVE_MAX_ATTEMPTS)
            await asyncio.sleep(10)
    raise StartupApiError("URL resolution failed")


async def _fetch(url: str) -> dict[str, Any]:
    settings = get_settings()
    for attempt in range(FETCH_MAX_ATTEMPTS):
        try:
            return await fetch_startup_payload(url, settings.storage_state_file)
        except LoginRequiredError as exc:
            if attempt >= FETCH_MAX_ATTEMPTS - 1:
                raise
            wait = settings.login_check_interval_seconds + 10
            logger.warning("history_api: login required, waiting {}s (attempt {}/{}): {}", wait, attempt + 1, FETCH_MAX_ATTEMPTS, exc)
            await asyncio.sleep(wait)
    raise LoginRequiredError("Login failed after retries")


def _page_oldest_item(items: list[NewsItem]) -> NewsItem | None:
    dated = [item for item in items if item.published_at is not None]
    if dated:
        return min(dated, key=lambda item: item.published_at or datetime.max)
    if items:
        return min(items, key=lambda item: int(item.external_id) if item.external_id.isdigit() else 0)
    return None



MAX_CONSECUTIVE_ERRORS = 3
URL_RESOLVE_MAX_ATTEMPTS = 12
FETCH_MAX_ATTEMPTS = 3


def _insert_items(items, source_method: str = SOURCE_METHOD_HISTORY, task_id: int = 0) -> tuple[int, int]:
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        inserted, skipped, new_ids, min_skipped_id = NewsRepository(conn).insert_many(
            items, source_method=source_method, task_id=task_id
        )
    if new_ids:
        print(f"history_api new NewsIDs: {new_ids}", flush=True)
    if skipped > 0 and min_skipped_id is not None:
        print(
            f"history_api skipped={skipped} min_skipped_id={min_skipped_id}",
            flush=True,
        )
    return inserted, skipped


async def run_history_api(task_id: int) -> None:
    """获取给定 task_id 的历史新闻页面，直到达到 end_time。"""
    settings = get_settings()

    # --- 加载任务行 ---
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        task_row = TaskRepository(conn).get(task_id)

    if task_row is None:
        logger.error("history_api: task_id={} not found", task_id)
        return

    if task_row["task_type"] != "history_api":
        logger.error(
            "history_api: task_id={} has unexpected type '{}'",
            task_id,
            task_row["task_type"],
        )
        return

    end_time_str: str | None = task_row["end_time"]
    end_news_id: int | None = task_row["end_news_id"]
    # 优先从 news 表查询该任务实际存储的最大 NewsID，确保从已持久化的位置继续
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        row = conn.execute(
            "SELECT MAX(NewsID) AS max_id FROM news WHERE task_id = ?", (task_id,)
        ).fetchone()
    db_max_for_task: int | None = int(row["max_id"]) if row and row["max_id"] else None
    current_news_id: int = db_max_for_task or task_row["start_news_id"] or 0
    inserted_total: int = 0
    skipped_total: int = 0

    # 解析 end_time
    end_time: datetime | None = None
    if end_time_str:
        try:
            end_time = datetime.fromisoformat(end_time_str)
        except ValueError:
            logger.error(
                "history_api: invalid end_time '{}' for task_id={}", end_time_str, task_id
            )

    logger.info(
        "history_api: starting task_id={} current_news_id={} end_time={}",
        task_id,
        current_news_id,
        end_time_str,
    )

    # --- 等待登录 ---
    login_status = read_login_status()
    if login_status != "ok":
        logger.info(
            "history_api: login_status='{}', waiting before starting", login_status
        )
        if not wait_for_login(
            timeout_seconds=settings.login_retry_attempts
            * settings.login_retry_interval_seconds
        ):
            logger.error("history_api: giving up waiting for login, exiting")
            with closing(connect(settings.database_file)) as conn:
                init_db(conn)
                TaskRepository(conn).update(
                    task_id, "error", last_error="login timeout"
                )
            return

    # 将任务标记为运行中
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        TaskRepository(conn).update(
            task_id, "running", pid=os.getpid(),
            pages=0, inserted=0, skipped=0,
            current_old_id=str(current_news_id),
            message=f"starting history_api task id={task_id}",
            last_started_at=cst_now(), last_error="",
        )

    # --- 解析 startup API URL ---
    base_url: str
    try:
        base_url = await _resolve_url()
    except StartupApiError as exc:
        logger.error("history_api: cannot resolve startup URL: {}", exc)
        with closing(connect(settings.database_file)) as conn:
            init_db(conn)
            TaskRepository(conn).update(
                task_id, "error", last_error=str(exc),
                message="URL resolution failed", last_finished_at=cst_now(),
            )
        return

    pages = 0
    consecutive_errors = 0

    # --- 主分页循环 ---
    while True:
        # 构建当前页面的 URL
        request_url = startup_url_with_old_id(base_url, current_news_id)
        with closing(connect(settings.database_file)) as conn:
            init_db(conn)
            TaskRepository(conn).update(
                task_id, "running", pages=pages,
                current_old_id=str(current_news_id),
                message=f"history_api task id={task_id} page={pages + 1} old_id={current_news_id}",
            )

        # 获取页面数据
        try:
            payload = await _fetch(request_url)
            consecutive_errors = 0
        except LoginRequiredError as exc:
            logger.warning(
                "history_api: task_id={} login required, waiting: {}", task_id, exc
            )
            with closing(connect(settings.database_file)) as conn:
                init_db(conn)
                TaskRepository(conn).update(
                    task_id, "paused", message="login required, waiting", last_error=str(exc),
                )
            # 等待登录；不要退出 — 当 login_status 变为 'ok' 时恢复
            wait_for_login(
                timeout_seconds=settings.login_retry_attempts
                * settings.login_retry_interval_seconds
            )
            continue
        except StartupApiError as exc:
            consecutive_errors += 1
            logger.warning(
                "history_api: task_id={} fetch error ({}/{}): {}",
                task_id,
                consecutive_errors,
                MAX_CONSECUTIVE_ERRORS,
                exc,
            )
            with closing(connect(settings.database_file)) as conn:
                init_db(conn)
                TaskRepository(conn).update(
                    task_id, "error", message="fetch error, retrying", last_error=str(exc),
                )
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                settings.startup_api_cache_file.unlink(missing_ok=True)
                consecutive_errors = 0
                try:
                    base_url = await _resolve_url()
                    logger.info("history_api: task_id={} URL refreshed", task_id)
                except StartupApiError:
                    logger.error("history_api: task_id={} URL refresh failed", task_id)
            jitter = random.uniform(-settings.interval_jitter_seconds, settings.interval_jitter_seconds)
            await asyncio.sleep(max(30, settings.history_sync_interval_seconds + jitter))
            continue

        # 解析条目
        items = parse_startup_payload(payload)  # type: ignore[arg-type]
        pages += 1

        # 停止条件：响应为空
        if not items:
            logger.info(
                "history_api: task_id={} complete: no more items at old_id={}",
                task_id,
                current_news_id,
            )
            with closing(connect(settings.database_file)) as conn:
                init_db(conn)
                TaskRepository(conn).update(
                    task_id, "completed", pages=pages,
                    inserted=inserted_total, skipped=skipped_total,
                    message=f"completed: no more items at old_id={current_news_id}",
                    last_finished_at=cst_now(), last_error="",
                )
            break

        # 插入前筛选出在时间范围内的条目
        eligible = items
        if end_time is not None:
            eligible = [
                i for i in items if i.published_at is None or i.published_at >= end_time
            ]

        inserted, skipped = _insert_items(eligible, source_method=SOURCE_METHOD_HISTORY, task_id=task_id)
        inserted_total += inserted
        skipped_total += skipped

        oldest_item = _page_oldest_item(items)
        oldest_at = oldest_item.published_at if oldest_item else None
        next_id = _batch_min_id(items, current_news_id)

        with closing(connect(settings.database_file)) as conn:
            init_db(conn)
            TaskRepository(conn).update(
                task_id, "running", pages=pages,
                current_news_id=next_id,
                inserted=inserted_total, skipped=skipped_total,
                current_old_id=str(next_id),
                current_oldest_at=oldest_at.strftime("%Y-%m-%d %H:%M:%S") if oldest_at else None,
                message=(
                    f"history_api task id={task_id} page={pages} "
                    f"inserted={inserted_total} skipped={skipped_total} current={next_id}"
                ),
                last_error="",
            )
        logger.info(
            "history_api task_id={} page={} inserted={} skipped={} current={}",
            task_id,
            pages,
            inserted_total,
            skipped_total,
            next_id,
        )
        print(
            f"history_api task_id={task_id} page={pages} "
            f"inserted={inserted_total} skipped={skipped_total} current={next_id}",
            flush=True,
        )

        # 停止条件：回溯到断档边界
        if end_news_id is not None and next_id <= end_news_id:
            logger.info(
                "history_api: task_id={} complete: reached end_news_id={}",
                task_id,
                end_news_id,
            )
            with closing(connect(settings.database_file)) as conn:
                init_db(conn)
                TaskRepository(conn).update(
                    task_id, "completed", pages=pages,
                    inserted=inserted_total, skipped=skipped_total,
                    message=f"completed: reached end_news_id={end_news_id}",
                    last_finished_at=cst_now(), last_error="",
                )
            break

        # 停止条件：最早条目的时间早于 end_time
        reached_end = (
            end_time is not None and oldest_at is not None and oldest_at < end_time
        )
        if reached_end:
            logger.info(
                "history_api: task_id={} complete: oldest_at={} < end_time={}",
                task_id,
                oldest_at,
                end_time_str,
            )
            with closing(connect(settings.database_file)) as conn:
                init_db(conn)
                TaskRepository(conn).update(
                    task_id, "completed", pages=pages,
                    inserted=inserted_total, skipped=skipped_total,
                    message=f"completed: reached end_time={end_time_str}",
                    last_finished_at=cst_now(), last_error="",
                )
            break

        # 保护：停滞检测（next_id 应小于 current）
        if next_id >= current_news_id:
            logger.warning(
                "history_api: task_id={} stalled at old_id={}, marking completed",
                task_id,
                next_id,
            )
            with closing(connect(settings.database_file)) as conn:
                init_db(conn)
                TaskRepository(conn).update(
                    task_id, "completed", pages=pages,
                    inserted=inserted_total, skipped=skipped_total,
                    message=f"completed (stall): old_id={next_id}",
                    last_finished_at=cst_now(), last_error="",
                )
            break

        current_news_id = next_id

        # 页面间限流
        jitter = random.uniform(-settings.interval_jitter_seconds, settings.interval_jitter_seconds)
        await asyncio.sleep(max(1, settings.history_sync_interval_seconds + jitter))


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m app.services.history_api <task_id>", file=sys.stderr)
        sys.exit(1)

    try:
        task_id = int(sys.argv[1])
    except ValueError:
        print(f"Invalid task_id: {sys.argv[1]!r}", file=sys.stderr)
        sys.exit(1)

    settings = get_settings()
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        str(settings.log_dir / "history_api.log"),
        rotation="10 MB",
        retention=5,
        encoding="utf-8",
        level="DEBUG",
    )

    try:
        asyncio.run(run_history_api(task_id))
    except KeyboardInterrupt:
        logger.info("history_api: stopped by user")
        print("history_api stopped.", flush=True)


if __name__ == "__main__":
    main()

