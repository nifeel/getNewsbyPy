"""主业务机器人。

作为独立进程运行（不依赖 server.py）。功能：

1. 启动 ``realtime_browser``（登录管理器）并等待登录完成。
2. 调用 ``collect_startup_news()`` 初始化数据库，获取
   ``latest_news_id`` / ``batch_min``。
3. 根据数据库状态（空库/断档/恢复）决定创建哪些任务。
4. 启动所有待处理的子进程任务。
5. 循环监控一切：登录失败时暂停任务，重启崩溃的工作进程，
   当前历史片段完成后调度下一个历史片段。

入口点::

    python -m app.robot.robot
"""

import asyncio
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import closing
from pathlib import Path

from loguru import logger

from app.collectors.startup_collector import collect_startup_news
from app.config import get_settings
from app.services.history_api import history_resume_start_id
from app.services.login_utils import read_login_status, wait_for_login
from app.storage.database import connect, init_db
from app.storage.repository import NewsRepository
from app.storage.task_repository import TaskRepository
from app.storage.task_status import cst_now, utc_now


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 映射 task_type 到 Python 模块名
_MODULE_MAP: dict[str, str] = {
    "realtime_browser": "app.services.realtime_browser",
    "history_api": "app.services.history_api",
}


# ---------------------------------------------------------------------------
# 子进程管理
# ---------------------------------------------------------------------------

# 键: "<task_type>_<task_id>"
processes: dict[str, subprocess.Popen] = {}
# 记录我们主动停止的键，避免自动重启它们
_stopping: set[str] = set()


def _proc_key(task_type: str, task_id: int | None = None) -> str:
    if task_id is None:
        return task_type
    return f"{task_type}_{task_id}"


def start_service(task_type: str, task_id: int | None = None) -> subprocess.Popen:
    """以子进程方式启动服务模块。

    Args:
        task_type: _MODULE_MAP 中的键之一。
        task_id: ``tasks`` 表中的行 ID。

    Returns:
        新启动的 :class:`subprocess.Popen` 对象。
    """
    # 单例保护：同一 task_type 不允许启动多个实例
    for key in list(processes.keys()):
        if not key.startswith(f"{task_type}_"):
            continue
        existing = processes.get(key)
        if existing and existing.poll() is None:
            logger.warning(
                "robot: {} already running (key={} pid={}), refusing duplicate",
                task_type, key, existing.pid,
            )
            return existing
        processes.pop(key, None)

    module = _MODULE_MAP[task_type]

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = os.pathsep.join(
        item for item in [str(PROJECT_ROOT), env.get("PYTHONPATH", "")] if item
    )

    command: list[str] = [sys.executable, "-m", module]
    if task_id is not None:
        command.append(str(task_id))

    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    proc = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        startupinfo=startupinfo,
        start_new_session=(os.name != "nt"),
    )

    key = _proc_key(task_type, task_id)
    processes[key] = proc
    logger.info("robot: started {} pid={} (key={})", module, proc.pid, key)

    # 在守护线程中将输出流式传输到 robot 日志
    threading.Thread(
        target=_stream_output,
        args=(key, proc),
        daemon=True,
    ).start()

    return proc


def _stream_output(key: str, proc: subprocess.Popen) -> None:
    """将子进程的标准输出/错误转发到 robot 日志器。"""
    if proc.stdout:
        for line in proc.stdout:
            logger.debug("{}: {}", key, line.rstrip())
    rc = proc.wait()
    logger.info("robot: {} exited code={}", key, rc)


def _terminate_proc(proc: subprocess.Popen) -> None:
    """结束子进程及其进程组（Chrome 是采集进程的孙进程）。"""
    if proc.poll() is not None:
        return
    if os.name != "nt" and proc.pid:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
    else:
        proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        if os.name != "nt" and proc.pid:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
        else:
            proc.kill()
        proc.wait(timeout=5)


def _stop_process(key: str) -> None:
    """优雅地终止一个被跟踪的子进程。"""
    proc = processes.pop(key, None)
    if proc is None:
        return
    _stopping.add(key)
    logger.info("robot: stopping {} pid={}", key, proc.pid)
    _terminate_proc(proc)
    _stopping.discard(key)


def _stop_all() -> None:
    """停止所有被跟踪的子进程。"""
    for key in list(processes.keys()):
        _stop_process(key)


# ---------------------------------------------------------------------------
# gui_settings 辅助函数
# ---------------------------------------------------------------------------


def _get_gui_setting(key: str, default: str = "") -> str:
    settings = get_settings()
    try:
        with closing(connect(settings.database_file)) as conn:
            init_db(conn)
            row = conn.execute(
                "SELECT value FROM gui_settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default
    except Exception as exc:
        logger.debug("robot: could not read gui_settings.{}: {}", key, exc)
        return default


def _set_gui_setting(key: str, value: str) -> None:
    settings = get_settings()
    try:
        with closing(connect(settings.database_file)) as conn:
            init_db(conn)
            conn.execute(
                """
                INSERT INTO gui_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, cst_now()),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("robot: could not write gui_settings.{}: {}", key, exc)


# ---------------------------------------------------------------------------
# 数据库辅助函数
# ---------------------------------------------------------------------------


def _db_is_empty() -> bool:
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        count = conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    return int(count) == 0


def _db_max_news_id() -> int:
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        return NewsRepository(conn).max_news_id()


def _create_task(
    task_type: str,
    *,
    start_news_id: int | None = None,
    end_news_id: int | None = None,
    end_time: str | None = None,
) -> int:
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        task_id = TaskRepository(conn).create(
            task_type,
            start_news_id=start_news_id,
            end_news_id=end_news_id,
            end_time=end_time,
        )
    logger.info(
        "robot: created task type={} id={} start={} end_news_id={} end_time={}",
        task_type,
        task_id,
        start_news_id,
        end_news_id,
        end_time,
    )
    return task_id


def _get_active_task(task_type: str):
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        return TaskRepository(conn).get_active(task_type)


def _has_active_task(task_type: str) -> bool:
    return _get_active_task(task_type) is not None


def _get_pending_tasks(task_type: str) -> list:
    """返回指定 task_type 的所有待处理行。"""
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        cursor = conn.execute(
            "SELECT * FROM tasks WHERE task_type = ? AND status = 'pending'",
            (task_type,),
        )
        return list(cursor.fetchall())


def _all_pending_tasks() -> list:
    """返回所有状态为 'pending' 的任务。"""
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        cursor = conn.execute(
            "SELECT * FROM tasks WHERE status = 'pending' ORDER BY created_at",
        )
        return list(cursor.fetchall())


def _get_all_running_tasks() -> list:
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        cursor = conn.execute(
            "SELECT * FROM tasks WHERE status IN ('running', 'paused')"
        )
        return list(cursor.fetchall())




def _update_task_status_db(task_id: int, status: str, *, pid: int | None = None) -> None:
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        TaskRepository(conn).update(task_id, status, pid=pid)


def _next_gap_task():
    """返回最高优先级的待处理 gap 任务（end_news_id 已设置，end_time 为 NULL）。"""
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        return TaskRepository(conn).get_next_gap_task()


def _next_history_task():
    """返回下一个非终止状态的 history_api 任务行，或 None。"""
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        return TaskRepository(conn).get_next_history_task()


def _has_active_gap_task(start_news_id: int, end_news_id: int) -> bool:
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        return TaskRepository(conn).has_active_gap_task_covering(start_news_id, end_news_id)


def _history_max_start_id() -> int | None:
    """返回未失败的历史任务中最高的 start_news_id。

    用作补缺任务的下界：无需重新获取已有历史任务覆盖的数据。
    """
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT MAX(start_news_id) AS max_id FROM tasks
            WHERE task_type = 'history_api'
              AND status NOT IN ('stopped', 'error')
              AND start_news_id IS NOT NULL
            """
        ).fetchone()
    return int(row["max_id"]) if row and row["max_id"] else None


def _get_all_incomplete_history() -> list:
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        return TaskRepository(conn).get_all_incomplete_history()


def _history_target_reached(history_since: str) -> bool:
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT 1 FROM tasks
            WHERE task_type = 'history_api'
              AND end_time = ?
              AND status = 'completed'
              AND message LIKE 'completed: reached end_time%'
            LIMIT 1
            """,
            (history_since,),
        ).fetchone()
    return row is not None


def _history_checkpoints(history_since: str) -> list[int]:
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT current_news_id FROM tasks
            WHERE task_type = 'history_api'
              AND end_time = ?
              AND current_news_id IS NOT NULL
              AND current_news_id > 0
            """,
            (history_since,),
        ).fetchall()
    return [int(row["current_news_id"]) for row in rows]


# ---------------------------------------------------------------------------
# 启动逻辑
# ---------------------------------------------------------------------------



def startup_inner() -> None:
    """收集启动新闻、决定创建哪些任务并启动它们。

    登录确认后调用——可能在 startup() 中立即执行，或在监控循环中登录最终成功后延迟执行。
    """
    # ------------------------------------------------------------------
    # 1. 收集启动新闻 → 获取 latest_news_id 和 batch_min
    # ------------------------------------------------------------------
    db_max_before = _db_max_news_id()
    # 绑定到当前实时采集的任务 ID
    rb_task_id = 0
    rb_row = _get_active_task("realtime_browser")
    if rb_row is not None:
        rb_task_id = int(rb_row["id"])
    logger.info("robot: collecting startup news batch (task_id={})", rb_task_id)
    try:
        inserted, skipped, total, batch_min = asyncio.run(collect_startup_news(task_id=rb_task_id))
    except Exception as exc:
        logger.error("robot: startup collection failed: {}; aborting startup", exc)
        return

    latest_news_id = _db_max_news_id()
    logger.info(
        "robot: startup batch done inserted={} skipped={} total={} "
        "batch_min={} latest_news_id={}",
        inserted,
        skipped,
        total,
        batch_min,
        latest_news_id,
    )

    # ------------------------------------------------------------------
    # 2. 决定创建哪些任务
    # ------------------------------------------------------------------
    db_was_empty = (total - inserted) == 0  # 所有行都是新增的 → 之前是空库

    if db_was_empty:
        logger.info("robot: DB was empty, creating initial tasks")
        _case_a_empty_db(latest_news_id)
    else:
        gap_exists = batch_min > 0 and batch_min > db_max_before + 1
        if gap_exists:
            logger.info(
                "robot: gap detected batch_min={} db_max={}; checking for existing gap task",
                batch_min,
                db_max_before,
            )
            _case_b_gap(batch_min, db_max_before)

        incomplete = _get_all_incomplete_history()
        if incomplete:
            logger.info("robot: {} incomplete history task(s) found, will resume", len(incomplete))
            _case_c_resume_history(incomplete)

        # 无 gap 也无待恢复任务时：如果 latest 超过了 db_max_before（Startup 新增了数据），创建小范围 gap 任务
        if not gap_exists and not incomplete and latest_news_id > db_max_before:
            _create_task("history_api", start_news_id=latest_news_id, end_news_id=db_max_before)
            logger.info(
                "robot: auto-created history_api gap task {}->{}",
                latest_news_id, db_max_before,
            )

    # ------------------------------------------------------------------
    # 3. 将 realtime_browser 的 start/current_news_id 锚定到启动时的最大值。
    #    这里设置 start_news_id 是因为该任务在 startup() 中预创建时
    #    latest_news_id 还未确定。
    #    仅在当前为 NULL（尚无实时数据）时设置 current_news_id，
    #    这样如果推送到达前登录失败，断档检测仍能正常运作。
    # ------------------------------------------------------------------
    if latest_news_id:
        rb_row = _get_active_task("realtime_browser")
        if rb_row is not None:
            settings = get_settings()
            with closing(connect(settings.database_file)) as conn:
                init_db(conn)
                if not rb_row["start_news_id"]:
                    conn.execute(
                        "UPDATE tasks SET start_news_id = ? WHERE id = ?",
                        (latest_news_id, int(rb_row["id"])),
                    )
                    conn.commit()
                    logger.info(
                        "robot: set realtime_browser id={} start_news_id={}",
                        rb_row["id"], latest_news_id,
                    )
                if not rb_row["current_news_id"]:
                    TaskRepository(conn).update(
                        int(rb_row["id"]), rb_row["status"],
                        current_news_id=latest_news_id,
                    )
                    logger.info(
                        "robot: anchored realtime_browser id={} current_news_id={}",
                        rb_row["id"], latest_news_id,
                    )

    # ------------------------------------------------------------------
    # 4. 启动所有待处理任务
    # ------------------------------------------------------------------
    _start_pending_tasks()


def startup() -> bool:
    """执行完整的启动序列。登录成功返回 True。"""
    settings = get_settings()

    # ------------------------------------------------------------------
    # 0. 清理上一次崩溃遗留的陈旧运行中任务
    # ------------------------------------------------------------------
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        affected = conn.execute(
            "UPDATE tasks SET status = 'pending', updated_at = ? "
            "WHERE status = 'running'",
            (utc_now(),),
        ).rowcount
        affected += conn.execute(
            "UPDATE tasks SET status = 'pending', updated_at = ? "
            "WHERE task_type = 'history_api' AND status = 'stopped'",
            (utc_now(),),
        ).rowcount
        conn.commit()
    if affected:
        logger.warning("robot: reset {} stale task(s) to pending", affected)

    # ------------------------------------------------------------------
    # 1. 启动 realtime_browser（登录管理器 + 可选收集器）
    # ------------------------------------------------------------------
    logger.info("robot: launching realtime_browser (login manager)")
    existing_rb = _get_active_task("realtime_browser")
    if existing_rb is not None:
        rb_task_id = int(existing_rb["id"])
        logger.info("robot: resuming realtime_browser task id={}", rb_task_id)
    else:
        rb_task_id = _create_task("realtime_browser")
        logger.info("robot: created realtime_browser task id={}", rb_task_id)
    proc = start_service("realtime_browser", rb_task_id)
    _update_task_status_db(rb_task_id, "running", pid=proc.pid)

    # ------------------------------------------------------------------
    # 2. 等待登录（最长 5 分钟）
    # ------------------------------------------------------------------
    logged_in = wait_for_login(timeout_seconds=300)
    if not logged_in:
        logger.error(
            "robot: login not confirmed after 5 min — "
            "no tasks will start until login succeeds"
        )
        return False

    startup_inner()
    return True


def _case_a_empty_db(latest_news_id: int) -> None:
    """为空数据库创建初始任务。"""
    settings = get_settings()

    if not _has_active_task("realtime_browser"):
        task_id = _create_task("realtime_browser", start_news_id=latest_news_id)
        logger.info("robot: created realtime_browser task id={}", task_id)

    history_since = _get_gui_setting("history_since", "")
    if history_since and not _has_active_task("history_api"):
        _create_task("history_api", start_news_id=latest_news_id, end_time=history_since)
        logger.info("robot: created history_api task (history_since={})", history_since)


def _case_b_gap(batch_min: int, db_max: int) -> None:
    """如果尚无任务覆盖该范围，则创建补缺的 history_api 任务。"""
    if _has_active_gap_task(batch_min, db_max):
        logger.info(
            "robot: active gap task already covers [{}, {}], skipping", db_max, batch_min
        )
        return
    task_id = _create_task(
        "history_api",
        start_news_id=batch_min,
        end_news_id=db_max,
    )
    logger.info(
        "robot: created gap history_api task id={} start={} end_news_id={}",
        task_id,
        batch_min,
        db_max,
    )


def _case_c_resume_history(incomplete: list) -> None:
    """恢复第一个未完成的历史任务（将由 _start_pending_tasks 启动）。"""
    # 任务已在数据库中；_start_pending_tasks 会负责启动它们。
    # 记录发现的信息，方便操作员查看状态。
    for row in incomplete:
        logger.info(
            "robot: will resume history_api task id={} status={} current={}",
            row["id"],
            row["status"],
            row["current_news_id"],
        )


def _start_pending_tasks() -> None:
    """为每个状态为 'pending' 的任务启动一个子进程。"""
    pending = _all_pending_tasks()
    if not pending:
        logger.info("robot: no pending tasks to start")
        return

    for row in pending:
        task_id = int(row["id"])
        task_type = row["task_type"]
        key = _proc_key(task_type, task_id)

        # 跳过不知道如何运行的任务类型
        if task_type not in _MODULE_MAP:
            logger.warning("robot: unknown task_type '{}', skipping", task_type)
            continue

        # 如果已在跟踪中则跳过
        if key in processes and processes[key].poll() is None:
            logger.debug("robot: {} already running, skipping", key)
            continue

        proc = start_service(task_type, task_id)
        _update_task_status_db(task_id, "running", pid=proc.pid)
        logger.info("robot: launched task id={} type={} pid={}", task_id, task_type, proc.pid)


# ---------------------------------------------------------------------------
# 监控循环
# ---------------------------------------------------------------------------


def monitor_loop(startup_completed: bool = True) -> None:
    """主监控循环。持续运行直到 KeyboardInterrupt。"""
    settings = get_settings()
    interval = settings.robot_interval_seconds

    # 跟踪登录状态以检测状态变化
    _prev_login_status = read_login_status()
    _startup_done = startup_completed

    logger.info("robot: entering monitor loop (interval={}s)", interval)

    while True:
        time.sleep(interval)

        try:
            # 如果启动被中止（登录超时），一旦登录成功就重试
            if not _startup_done:
                current_status = read_login_status()
                if current_status == "ok":
                    logger.info("robot: login now ok — running deferred startup")
                    startup_inner()
                    _startup_done = True

            _monitor_tick(_prev_login_status)
            _prev_login_status = read_login_status()
        except Exception as exc:
            logger.exception("robot: monitor_tick error: {}", exc)


def _monitor_tick(prev_login_status: str) -> None:
    """单次监控心跳：处理登录状态变化、崩溃重启、历史任务调度。"""
    current_login_status = read_login_status()

    # ------------------------------------------------------------------
    # 1. 登录状态转换
    # ------------------------------------------------------------------
    if current_login_status == "failed" and prev_login_status != "failed":
        logger.warning("robot: login failed — pausing all running tasks")
        _pause_all_running()

    elif current_login_status == "ok" and prev_login_status == "failed":
        logger.info("robot: login restored — resuming paused tasks")
        _resume_paused_tasks()

    # ------------------------------------------------------------------
    # 2. 崩溃检测与重启
    # ------------------------------------------------------------------
    for key in list(processes.keys()):
        proc = processes.get(key)
        if proc is None:
            continue
        if proc.poll() is not None and key not in _stopping:
            processes.pop(key, None)
            parts = key.rsplit("_", 1)
            if len(parts) == 2 and parts[1].isdigit():
                task_id = int(parts[1])
                settings = get_settings()
                with closing(connect(settings.database_file)) as conn:
                    init_db(conn)
                    row = TaskRepository(conn).get(task_id)
                if row and row["status"] in ("completed", "stopped"):
                    logger.info("robot: {} finished normally (status={}), not restarting", key, row["status"])
                    continue
            logger.warning(
                "robot: {} (pid={}) died unexpectedly (code={}), restarting",
                key,
                proc.pid,
                proc.returncode,
            )
            _restart_from_key(key)

    # ------------------------------------------------------------------
    # 3. 实时收集器同步（仅登录状态下执行）
    # ------------------------------------------------------------------
    if current_login_status == "ok":
        _sync_browser_task()

        # ------------------------------------------------------------------
        # 4. 历史任务调度（仅登录状态下执行）
        # ------------------------------------------------------------------
        _ensure_history_task_exists()
        _maybe_dispatch_next_history()


def _pause_all_running() -> None:
    """暂停所有运行中/已暂停的任务：将数据库行标记为 'paused'，发送 SIGTERM 给子进程。"""
    running = _get_all_running_tasks()
    for row in running:
        task_id = int(row["id"])
        task_type = row["task_type"]
        if task_type in ("login_check", "realtime_browser"):
            continue
        key = _proc_key(task_type, task_id)

        logger.info("robot: pausing task id={} type={}", task_id, task_type)
        _update_task_status_db(task_id, "paused")

        proc = processes.get(key)
        if proc and proc.poll() is None:
            _stopping.add(key)
            _terminate_proc(proc)
            _stopping.discard(key)
            processes.pop(key, None)


def _resume_paused_tasks() -> None:
    """恢复因登录失败而暂停的任务。"""
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        paused_rows = list(
            conn.execute(
                "SELECT * FROM tasks WHERE status = 'paused'"
            ).fetchall()
        )

    current_max_id = _db_max_news_id()

    for row in paused_rows:
        task_id = int(row["id"])
        task_type = row["task_type"]

        if task_type not in _MODULE_MAP:
            continue

        # 实时收集器的断档检测
        if task_type == "realtime_browser":
            paused_at_id = row["current_news_id"]
            if paused_at_id and current_max_id > paused_at_id:
                if not _has_active_gap_task(current_max_id, paused_at_id):
                    gap_task_id = _create_task(
                        "history_api",
                        start_news_id=current_max_id,
                        end_news_id=paused_at_id,
                    )
                    logger.info(
                        "robot: created gap-fill task id={} start={} end={}",
                        gap_task_id,
                        current_max_id,
                        paused_at_id,
                    )
                else:
                    logger.info(
                        "robot: gap-fill task already exists for start={} end={}, skipping",
                        current_max_id,
                        paused_at_id,
                    )

        logger.info("robot: resuming task id={} type={}", task_id, task_type)
        with closing(connect(settings.database_file)) as conn:
            init_db(conn)
            TaskRepository(conn).update(task_id, "pending")

        proc = start_service(task_type, task_id)
        _update_task_status_db(task_id, "running", pid=proc.pid)
        logger.info(
            "robot: resumed task id={} type={} pid={}", task_id, task_type, proc.pid
        )

    # 恢复完成后如果没有待处理/运行中的历史任务，则创建一条新的
    _ensure_history_task_exists()
    _start_pending_tasks()


def _restart_from_key(key: str) -> None:
    """根据进程键重新启动服务。"""
    # 键格式："<task_type>" 或 "<task_type>_<task_id>"
    parts = key.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        task_type = parts[0]
        task_id: int | None = int(parts[1])
    else:
        task_type = key
        task_id = None

    if task_type not in _MODULE_MAP:
        logger.warning("robot: cannot restart unknown task_type '{}'", task_type)
        return

    logger.info("robot: restarting key={} task_type={} task_id={}", key, task_type, task_id)
    proc = start_service(task_type, task_id)
    if task_id is not None:
        _update_task_status_db(task_id, "running", pid=proc.pid)
    logger.info("robot: restarted {} as pid={}", key, proc.pid)


def _sync_browser_task() -> None:
    """确保 realtime_browser（登录管理器）始终运行。"""
    browser_key = next(
        (k for k in list(processes.keys()) if k.startswith("realtime_browser_")),
        None,
    )
    browser_running = (
        browser_key is not None
        and processes.get(browser_key) is not None
        and processes[browser_key].poll() is None
    )
    if browser_running or _has_active_task("realtime_browser"):
        return

    task_id = _create_task("realtime_browser", start_news_id=_db_max_news_id())
    proc = start_service("realtime_browser", task_id)
    _update_task_status_db(task_id, "running", pid=proc.pid)
    logger.info("robot: started realtime_browser task id={} pid={}", task_id, proc.pid)


def _ensure_history_task_exists() -> None:
    """如果没有活跃/待恢复的历史任务且配置了 history_since，则创建一条新的。"""
    if _get_all_incomplete_history():
        return
    if _has_active_task("history_api"):
        return
    history_since = _get_gui_setting("history_since", "")
    if not history_since:
        return
    start_id = history_resume_start_id(
        target_reached=_history_target_reached(history_since),
        checkpoints=_history_checkpoints(history_since),
        latest_news_id=_db_max_news_id(),
    )
    if start_id is None:
        return
    _create_task("history_api", start_news_id=start_id, end_time=history_since)
    logger.info(
        "robot: ensured history_api task (history_since={} start={})",
        history_since,
        start_id,
    )


def _maybe_dispatch_next_history() -> None:
    """调度下一个历史任务，gap 任务优先于全量历史扫描。"""
    # gap 任务优先（按 start_news_id DESC，即最新缺口优先）
    next_task = _next_gap_task()
    # 没有 gap 时才考虑全量历史任务
    if next_task is None:
        next_task = _next_history_task()
    if next_task is None:
        return

    next_id = int(next_task["id"])

    # 检查当前是否有正在运行的历史任务
    for key in list(processes.keys()):
        if not key.startswith("history_api_"):
            continue
        proc = processes.get(key)
        if proc is None or proc.poll() is not None:
            continue
        parts = key.rsplit("_", 1)
        if len(parts) != 2 or not parts[1].isdigit():
            continue
        running_id = int(parts[1])
        if running_id >= next_id:
            return  # 已经在运行正确的任务
        # 较旧的任务正在运行 — 暂停它以让较新的任务优先执行
        logger.info(
            "robot: pausing older history task id={} to run newer id={}",
            running_id, next_id,
        )
        _stop_process(key)
        _update_task_status_db(running_id, "pending")

    key = _proc_key("history_api", next_id)
    if key in processes and processes[key].poll() is None:
        return
    logger.info("robot: dispatching history_api task id={}", next_id)
    proc = start_service("history_api", next_id)
    _update_task_status_db(next_id, "running", pid=proc.pid)
    logger.info("robot: dispatched history_api task id={} pid={}", next_id, proc.pid)


# ---------------------------------------------------------------------------
# 入口点
# ---------------------------------------------------------------------------


def _prevent_sleep() -> "subprocess.Popen | None":
    """阻止系统休眠。macOS 上返回子进程对象，Windows/其他系统返回 None。"""
    if sys.platform == "darwin":
        try:
            proc = subprocess.Popen(
                ["caffeinate", "-i"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("robot: caffeinate started pid={}", proc.pid)
            return proc
        except FileNotFoundError:
            logger.warning("robot: caffeinate not found, system may sleep during tasks")
            return None
    elif sys.platform == "win32":
        try:
            import ctypes
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)  # type: ignore[attr-defined]
            logger.info("robot: Windows sleep prevention enabled")
        except Exception as exc:
            logger.warning("robot: could not prevent sleep on Windows: {}", exc)
        return None
    return None


def _allow_sleep(proc: "subprocess.Popen | None") -> None:
    """重新允许系统休眠。"""
    if sys.platform == "darwin":
        if proc is not None and proc.poll() is None:
            proc.terminate()
        logger.info("robot: caffeinate stopped")
    elif sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)  # type: ignore[attr-defined]
            logger.info("robot: Windows sleep prevention disabled")
        except Exception:
            pass


def main() -> None:
    settings = get_settings()
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        str(settings.log_dir / "robot.log"),
        rotation="10 MB",
        retention=5,
        encoding="utf-8",
        level="DEBUG",
    )

    logger.info("robot: starting pid={}", os.getpid())

    # 注册我们的 PID 以便 watchdog 可以监控我们
    _set_gui_setting("robot_pid", str(os.getpid()))

    # 收到 SIGTERM 时优雅关闭（Unix）
    def _handle_sigterm(signum, frame):  # type: ignore[no-untyped-def]
        raise KeyboardInterrupt

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_sigterm)

    sleep_proc = _prevent_sleep()
    try:
        login_ok = startup()
        monitor_loop(startup_completed=login_ok)
    except KeyboardInterrupt:
        logger.info("robot: shutting down")
        _stop_all()
        logger.info("robot: all sub-processes stopped, exiting")
    finally:
        _allow_sleep(sleep_proc)


if __name__ == "__main__":
    main()
