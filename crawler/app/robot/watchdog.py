"""看门狗进程。

监控 robot 进程，若其死亡则重新启动。同时定期重启自身（通过 ``os.execv``）
以避免内存膨胀。

入口点::

    python -m app.robot.watchdog

使用的配置项:

* ``settings.watchdog_check_interval_seconds``  – 检查 robot 进程的频率
* ``settings.watchdog_restart_interval_seconds`` – 自动重启自身的时间间隔
* ``settings.log_dir``                           – watchdog.log 写入目录
"""

import os
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

from loguru import logger


from app.config import get_settings

from app.storage.database import connect, init_db
from app.storage.task_log import write_task_log
from app.storage.task_status import cst_now


PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 进程存活检测
# ---------------------------------------------------------------------------


def is_process_alive(pid: int) -> bool:
    """如果 *pid* 对应的进程仍在运行则返回 True。

    使用 ``os.kill(pid, 0)``，该方法在 Unix 和 Windows 上均可工作
    （Python 3.11+ 的 Windows 支持通过 os.kill 发送信号 0）。
    在旧版 Windows 上回退到 ``tasklist`` 查询。
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        # ESRCH：进程不存在
        return False
    except PermissionError:
        # EPERM：进程存在但当前用户无权限
        return True
    except OSError:
        # 在信号 0 不受支持的平台上使用回退方案
        return _is_alive_fallback(pid)


def _is_alive_fallback(pid: int) -> bool:
    """使用平台特定命令的回退存活检测。"""
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return str(pid) in result.stdout
        except Exception:
            return False
    else:
        # Unix：检查 /proc/<pid>
        return os.path.exists(f"/proc/{pid}")


# ---------------------------------------------------------------------------
# gui_settings 辅助函数
# ---------------------------------------------------------------------------


def get_robot_pid() -> int | None:
    """从 gui_settings 读取 robot_pid；若不存在或无效则返回 None。"""
    settings = get_settings()
    try:
        with closing(connect(settings.database_file)) as conn:
            init_db(conn)
            row = conn.execute(
                "SELECT value FROM gui_settings WHERE key = 'robot_pid'"
            ).fetchone()
        if row is None:
            return None
        value = (row["value"] or "").strip()
        if not value:
            return None
        return int(value)
    except Exception as exc:
        logger.debug("watchdog: could not read robot_pid: {}", exc)
        return None


def _set_robot_pid(pid: int) -> None:
    settings = get_settings()
    try:
        with closing(connect(settings.database_file)) as conn:
            init_db(conn)
            conn.execute(
                """
                INSERT INTO gui_settings (key, value, updated_at)
                VALUES ('robot_pid', ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (str(pid), cst_now()),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("watchdog: could not write robot_pid: {}", exc)


# ---------------------------------------------------------------------------
# 双写日志
# ---------------------------------------------------------------------------


def _log(level: str, message: str) -> None:
    """同时写入 loguru 和 task_logs 表。"""
    log_func = getattr(logger, level.lower(), logger.info)
    log_func("watchdog: {}", message)
    try:
        write_task_log(get_settings().database_file, 0, "watchdog", level, message)
    except Exception as exc:
        logger.warning("watchdog: task_log write failed: {}", exc)


# ---------------------------------------------------------------------------
# Robot 启动器
# ---------------------------------------------------------------------------


def start_robot() -> subprocess.Popen:
    """启动 ``python -m app.robot.robot`` 并返回 Popen 句柄。"""
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = os.pathsep.join(
        item for item in [str(PROJECT_ROOT), env.get("PYTHONPATH", "")] if item
    )

    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    proc = subprocess.Popen(
        [sys.executable, "-m", "app.robot.robot"],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        startupinfo=startupinfo,
    )

    _log("INFO", "started robot pid={}".format(proc.pid))
    _set_robot_pid(proc.pid)
    return proc


# ---------------------------------------------------------------------------
# 实时任务心跳检测
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 入口点
# ---------------------------------------------------------------------------


def main() -> None:
    settings = get_settings()
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        str(settings.log_dir / "watchdog.log"),
        rotation="10 MB",
        retention=5,
        encoding="utf-8",
        level="DEBUG",
    )

    check_interval = settings.watchdog_check_interval_seconds
    restart_interval = settings.watchdog_restart_interval_seconds
    start_time = time.monotonic()

    _log("INFO",
        "started pid={} check_interval={}s self_restart_interval={}s".format(
            os.getpid(), check_interval, restart_interval
        )
    )

    try:
        while True:
            # 如果运行时间足够长则自我重启
            elapsed = time.monotonic() - start_time
            if elapsed >= restart_interval:
                _log("INFO",
                    "self-restarting after {:.0f}s (limit={}s)".format(
                        elapsed, restart_interval
                    )
                )
                os.execv(sys.executable, [sys.executable, "-m", "app.robot.watchdog"])
                # os.execv 会替换当前进程；此后的代码不会执行。

            # ------------------------------------------------------------------
            # 检查 robot 进程存活状态
            # ------------------------------------------------------------------
            robot_pid = get_robot_pid()

            if robot_pid is None:
                _log("WARNING", "no robot_pid found; starting robot")
                start_robot()
            elif is_process_alive(robot_pid):
                logger.debug("watchdog: robot pid={} is alive", robot_pid)
            else:
                _log("WARNING", "robot pid={} is dead; restarting".format(robot_pid))
                start_robot()

            time.sleep(check_interval)

    except KeyboardInterrupt:
        _log("INFO", "stopped by user")


if __name__ == "__main__":
    main()
