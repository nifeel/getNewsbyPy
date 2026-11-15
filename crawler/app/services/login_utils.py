"""登录状态查询工具。

供 robot、history_api、realtime_api 等模块共用。
"""

import time
from contextlib import closing

from loguru import logger

from app.config import get_settings
from app.storage.database import connect, init_db


def read_login_status() -> str:
    """返回 gui_settings.login_status 的当前值（默认 'unknown'）。"""
    settings = get_settings()
    try:
        with closing(connect(settings.database_file)) as conn:
            init_db(conn)
            row = conn.execute(
                "SELECT value FROM gui_settings WHERE key = 'login_status'"
            ).fetchone()
            return row["value"] if row else "unknown"
    except Exception as exc:
        logger.debug("login_utils: could not read login_status: {}", exc)
        return "unknown"


def wait_for_login(timeout_seconds: int = 300, poll_seconds: int | None = None) -> bool:
    """轮询 gui_settings.login_status 直到 'ok' 或超时。

    Args:
        timeout_seconds: 最长等待秒数。
        poll_seconds: 轮询间隔秒数；默认取配置 login_check_interval_seconds。

    Returns:
        True 如果登录变为 'ok'，False 如果超时。
    """
    settings = get_settings()
    if poll_seconds is None:
        poll_seconds = settings.login_check_interval_seconds

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status = read_login_status()
        if status == "ok":
            logger.info("login_utils: login_status='ok'")
            return True
        remaining = int(deadline - time.monotonic())
        logger.info(
            "login_utils: login_status='{}', waiting ({}s remaining)...",
            status,
            remaining,
        )
        time.sleep(min(poll_seconds, max(1, remaining)))

    logger.warning(
        "login_utils: timed out waiting for login_status='ok' after {}s",
        timeout_seconds,
    )
    return False
