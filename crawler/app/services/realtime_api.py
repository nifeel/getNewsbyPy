"""实时 API 收集服务。

以固定间隔轮询 FinancialJuice Startup API，将新的新闻条目
以 ``source_method='Startup'`` 持久化到 ``news`` 表中。

用法::

    python -m app.services.realtime_api <task_id>

*task_id* 参数必须引用 ``tasks`` 表中
``task_type='realtime_api'`` 的现有行。
"""

import asyncio
import os
import random
import sys
from contextlib import closing

from loguru import logger

from app.collectors.startup_collector import collect_startup_news
from app.config import get_settings
from app.parsers.financialjuice import parse_startup_payload
from app.storage.database import connect, init_db, SOURCE_METHOD_STARTUP
from app.storage.repository import NewsRepository
from app.storage.task_repository import TaskRepository
from app.storage.task_status import cst_now
from app.services.login_utils import wait_for_login

TASK_NAME = "realtime_api"


# ---------------------------------------------------------------------------
# 主协程
# ---------------------------------------------------------------------------


async def run_realtime_api(task_id: int) -> None:
    """循环收集新闻，每次周期结束后更新给定 *task_id* 的行。

    Args:
        task_id: ``tasks`` 表中跟踪本次运行的行的主键。
    """
    settings = get_settings()

    # 将任务标记为运行中
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        TaskRepository(conn).update(
            task_id, "running", pid=os.getpid(), last_started_at=cst_now(), last_error=""
        )

    cycle = 0
    total_inserted = 0
    total_skipped = 0

    try:
        while True:
            # ------------------------------------------------------------------
            # 1. 门控：等待 login_checker 确认会话仍然有效
            # ------------------------------------------------------------------
            try:
                with closing(connect(settings.database_file)) as conn:
                    init_db(conn)
                    row = conn.execute(
                        "SELECT value FROM gui_settings WHERE key = 'login_status'"
                    ).fetchone()
                login_status = row["value"] if row else "unknown"
            except Exception as exc:
                logger.warning("realtime_api: could not read login_status: {}", exc)
                login_status = "unknown"

            if login_status != "ok":
                logger.info(
                    "realtime_api: login_status='{}', waiting up to 300s …", login_status
                )
                logged_in = await asyncio.to_thread(wait_for_login, 300)
                if not logged_in:
                    logger.warning("realtime_api: still not logged in; retrying next cycle.")
                    with closing(connect(settings.database_file)) as conn:
                        init_db(conn)
                        TaskRepository(conn).update(
                            task_id, "running", pid=os.getpid(),
                            message="waiting for login", last_error="login not ready",
                        )
                    await asyncio.sleep(settings.collector_retry_seconds)
                    continue

            # ------------------------------------------------------------------
            # 2. 从 Startup API 收集数据
            # ------------------------------------------------------------------
            cycle += 1
            logger.info("realtime_api: cycle {} — collecting …", cycle)
            with closing(connect(settings.database_file)) as conn:
                init_db(conn)
                TaskRepository(conn).update(
                    task_id, "running", pid=os.getpid(),
                    cycle=cycle, message=f"cycle {cycle}: collecting",
                )

            try:
                inserted, skipped, total, _batch_min = await collect_startup_news(task_id=task_id)
            except Exception as exc:
                logger.warning("realtime_api: collection error on cycle {}: {}", cycle, exc)
                with closing(connect(settings.database_file)) as conn:
                    init_db(conn)
                    TaskRepository(conn).update(
                        task_id, "running", pid=os.getpid(),
                        cycle=cycle, last_error=str(exc),
                        message=f"cycle {cycle}: error — {exc}",
                    )
                await asyncio.sleep(settings.collector_retry_seconds)
                continue

            total_inserted += inserted
            total_skipped += skipped

            # ------------------------------------------------------------------
            # 3. 以 source_method='Startup' 持久化数据
            #    collect_startup_news → store_startup_payload 已写入
            #    ``news`` 表。此处为新插入的行更新 source_method，
            #    使用刚插入的最大 NewsID。
            # ------------------------------------------------------------------
            with closing(connect(settings.database_file)) as conn:
                init_db(conn)
                repo = NewsRepository(conn)
                current_max_id = repo.max_news_id()

                # 为缺失 source_method 的行（新插入的）打上标记
                if inserted > 0:
                    conn.execute(
                        """
                        UPDATE news
                        SET source_method = ?
                        WHERE source_method = ''
                        """,
                        (SOURCE_METHOD_STARTUP,),
                    )
                    conn.commit()
                    logger.debug(
                        "realtime_api: stamped source_method='{}' on {} new row(s).",
                        SOURCE_METHOD_STARTUP,
                        inserted,
                    )

                # 更新 tasks.current_news_id
                TaskRepository(conn).update(
                    task_id,
                    "running",
                    current_news_id=current_max_id,
                    pid=os.getpid(),
                )

            logger.info(
                "realtime_api: cycle {} done — inserted={} skipped={} max_id={}",
                cycle,
                inserted,
                skipped,
                current_max_id,
            )

            with closing(connect(settings.database_file)) as conn:
                init_db(conn)
                TaskRepository(conn).update(
                    task_id, "running", pid=os.getpid(),
                    cycle=cycle, inserted=total_inserted, skipped=total_skipped, total=total,
                    last_finished_at=cst_now(), last_error="",
                    message=f"cycle {cycle}: inserted={inserted} skipped={skipped}",
                )

            # ------------------------------------------------------------------
            # 4. 等待下一个周期
            # ------------------------------------------------------------------
            jitter = random.uniform(-settings.interval_jitter_seconds, settings.interval_jitter_seconds)
            await asyncio.sleep(max(1, settings.realtime_interval_seconds + jitter))

    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("realtime_api: stopped.")
    except Exception as exc:
        logger.exception("realtime_api: unexpected error: {}", exc)
        with closing(connect(settings.database_file)) as conn:
            init_db(conn)
            TaskRepository(conn).update(task_id, "error", pid=os.getpid(), last_error=str(exc))
        raise
    else:
        pass
    finally:
        try:
            with closing(connect(settings.database_file)) as conn:
                init_db(conn)
                TaskRepository(conn).update(
                    task_id, "stopped", pid=os.getpid(),
                    last_finished_at=cst_now(), cycle=cycle,
                    inserted=total_inserted, skipped=total_skipped,
                )
        except Exception:
            pass


def main() -> None:
    """入口点：从 argv[1] 读取 task_id，配置日志，运行循环。"""
    if len(sys.argv) < 2:
        print(
            "Usage: python -m app.services.realtime_api <task_id>",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        task_id = int(sys.argv[1])
    except ValueError:
        print(f"Invalid task_id: {sys.argv[1]!r}", file=sys.stderr)
        sys.exit(1)

    settings = get_settings()
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        str(settings.log_dir / "realtime_api.log"),
        rotation="10 MB",
        retention=5,
        encoding="utf-8",
        level="DEBUG",
    )

    logger.info("realtime_api: starting with task_id={}", task_id)

    try:
        asyncio.run(run_realtime_api(task_id))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
