"""用腾讯云机器翻译补全 news.title_zh（en → zh）。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from contextlib import closing
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger

from app.config import get_settings
from app.storage.database import connect, init_db
from app.storage.task_status import cst_now

BATCH_SIZE = 40
IDLE_SLEEP_SECONDS = 60
ERROR_SLEEP_SECONDS = 15
MAX_ITEM_CHARS = 6000
MAX_BATCH_ITEMS = 100
MAX_REQUEST_CHARS = 5000
QUOTA_ERROR_CODES = (
    "FailedOperation.NoFreeAmount",
    "FailedOperation.StopUsing",
    "LimitExceeded",
)
KEY_INDEX_SETTING = "tmt_key_index"
KEY_MONTH_SETTING = "tmt_key_month"
BILLING_TZ = ZoneInfo("Asia/Shanghai")

_TMT_LOCK = threading.Lock()


class TranslateRequestError(ValueError):
    """调用方参数错误。"""


class TmtQuotaError(RuntimeError):
    """当前密钥额度用尽或账号停服。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"tmt error {code}: {message}")


def normalize_texts(raw: Any) -> list[str]:
    if isinstance(raw, str):
        texts = [raw]
    elif isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        texts = list(raw)
    else:
        raise TranslateRequestError("q 必须是 string 或 string 数组")
    if not texts:
        raise TranslateRequestError("q 不能为空")
    if len(texts) > MAX_BATCH_ITEMS:
        raise TranslateRequestError(f"单次最多 {MAX_BATCH_ITEMS} 条")
    total = 0
    for index, text in enumerate(texts):
        if not text.strip():
            raise TranslateRequestError(f"q[{index}] 不能为空")
        if len(text) > MAX_ITEM_CHARS:
            raise TranslateRequestError(f"q[{index}] 超过 {MAX_ITEM_CHARS} 字符")
        total += len(text)
    return texts


def chunked[T](items: Sequence[T], size: int) -> list[list[T]]:
    if size <= 0:
        raise ValueError("size must be positive")
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def is_quota_error(code: str, message: str = "") -> bool:
    text = f"{code} {message}"
    if any(item and item in text for item in QUOTA_ERROR_CODES):
        return True
    return "免费额度" in message and "用完" in message


def billing_month() -> str:
    return datetime.now(BILLING_TZ).strftime("%Y-%m")


def credential_pairs() -> list[tuple[str, str]]:
    settings = get_settings()
    pairs: list[tuple[str, str]] = []
    for secret_id, secret_key in (
        (settings.tmt_secret_id, settings.tmt_secret_key),
        (settings.tmt_secret_id_2, settings.tmt_secret_key_2),
    ):
        sid = (secret_id or "").strip()
        skey = (secret_key or "").strip()
        if sid and skey:
            pairs.append((sid, skey))
    return pairs


def _write_setting(connection, key: str, value: str) -> None:
    connection.execute(
        """
        INSERT INTO gui_settings (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (key, value, cst_now()),
    )


def load_key_index(n_keys: int) -> int:
    if n_keys <= 0:
        return 0
    month = billing_month()
    settings = get_settings()
    try:
        with closing(connect(settings.database_file)) as connection:
            init_db(connection)
            index_row = connection.execute(
                "SELECT value FROM gui_settings WHERE key = ?",
                (KEY_INDEX_SETTING,),
            ).fetchone()
            month_row = connection.execute(
                "SELECT value FROM gui_settings WHERE key = ?",
                (KEY_MONTH_SETTING,),
            ).fetchone()
            stored_month = (month_row["value"] if month_row else "") or ""
            raw_index = (index_row["value"] if index_row else "0") or "0"
            if stored_month != month:
                _write_setting(connection, KEY_INDEX_SETTING, "0")
                _write_setting(connection, KEY_MONTH_SETTING, month)
                connection.commit()
                return 0
            try:
                index = int(raw_index)
            except ValueError:
                index = 0
            if index < 0 or index >= n_keys:
                index = 0
            return index
    except Exception:
        logger.exception("translate_tmt: load key index failed")
        return 0


def save_key_index(index: int) -> None:
    settings = get_settings()
    try:
        with closing(connect(settings.database_file)) as connection:
            init_db(connection)
            _write_setting(connection, KEY_INDEX_SETTING, str(index))
            _write_setting(connection, KEY_MONTH_SETTING, billing_month())
            connection.commit()
    except Exception:
        logger.exception("translate_tmt: save key index failed")


def _translate_batch(
    texts: list[str],
    secret_id: str,
    secret_key: str,
    region: str,
    source: str,
    target: str,
) -> list[str]:
    from tencentcloud.common import credential
    from tencentcloud.common.common_client import CommonClient
    from tencentcloud.common.exception.tencent_cloud_sdk_exception import (
        TencentCloudSDKException,
    )

    cred = credential.Credential(secret_id, secret_key)
    client = CommonClient("tmt", "2018-03-21", cred, region)
    try:
        payload = client.call_json(
            "TextTranslateBatch",
            {
                "Source": source,
                "Target": target,
                "ProjectId": 0,
                "SourceTextList": texts,
            },
        )
    except TencentCloudSDKException as exc:
        code = str(getattr(exc, "code", "") or "")
        message = str(exc)
        if is_quota_error(code, message):
            raise TmtQuotaError(code or "FailedOperation.NoFreeAmount", message) from exc
        raise
    body = payload.get("Response") or {}
    if body.get("Error"):
        err = body["Error"]
        code = str(err.get("Code") or "")
        message = str(err.get("Message") or "")
        if is_quota_error(code, message):
            raise TmtQuotaError(code, message)
        raise RuntimeError(f"tmt error {code}: {message}")
    out = list(body.get("TargetTextList") or [])
    if len(out) != len(texts):
        raise RuntimeError(f"tmt batch size mismatch: sent={len(texts)} got={len(out)}")
    return out


def _translate_chunk(
    texts: list[str],
    source: str,
    target: str,
    pairs: list[tuple[str, str]],
    start_index: int,
    region: str,
    batch_fn: Callable[..., list[str]],
) -> tuple[list[str], int]:
    last_quota: TmtQuotaError | None = None
    n_keys = len(pairs)
    for offset in range(n_keys):
        index = (start_index + offset) % n_keys
        secret_id, secret_key = pairs[index]
        try:
            translated = batch_fn(texts, secret_id, secret_key, region, source, target)
        except TmtQuotaError as exc:
            last_quota = exc
            logger.warning("translate_tmt: key {} quota exhausted ({})", index, exc.code)
            continue
        return translated, index
    if last_quota is not None:
        raise last_quota
    raise RuntimeError("FJ_TMT_SECRET_ID / FJ_TMT_SECRET_KEY 未配置")


def translate_texts(texts: list[str], source: str = "en", target: str = "zh") -> list[str]:
    settings = get_settings()
    pairs = credential_pairs()
    if not pairs:
        raise RuntimeError("FJ_TMT_SECRET_ID / FJ_TMT_SECRET_KEY 未配置")
    out: list[str] = []
    with _TMT_LOCK:
        start_index = load_key_index(len(pairs))
        index = start_index
        for chunk in _split_by_request_limit(texts):
            translated, index = _translate_chunk(
                chunk,
                source,
                target,
                pairs,
                index,
                settings.tmt_region,
                _translate_batch,
            )
            out.extend(translated)
        if index != start_index:
            logger.warning("translate_tmt: switched credential index {} -> {}", start_index, index)
            save_key_index(index)
    return out


def _split_by_request_limit(texts: list[str]) -> list[list[str]]:
    chunks: list[list[str]] = []
    current: list[str] = []
    used = 0
    for text in texts:
        extra = len(text)
        if current and (len(current) >= MAX_BATCH_ITEMS or used + extra > MAX_REQUEST_CHARS):
            chunks.append(current)
            current = []
            used = 0
        current.append(text)
        used += extra
    if current:
        chunks.append(current)
    return chunks


def _fetch_pending(connection, limit: int) -> list[tuple[int, str]]:
    rows = connection.execute(
        """
        SELECT id, Title
        FROM news
        WHERE title_zh IS NULL
          AND Title IS NOT NULL
          AND Title <> ''
        ORDER BY COALESCE(DatePublished, fetched_at, created_at) DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    pending: list[tuple[int, str]] = []
    for row in rows:
        news_id = int(row["id"])
        title = str(row["Title"] or "").strip()
        if title:
            pending.append((news_id, title[:MAX_ITEM_CHARS]))
    return pending


def _pending_count(connection) -> int:
    row = connection.execute(
        """
        SELECT COUNT(*) AS n FROM news
        WHERE title_zh IS NULL AND Title IS NOT NULL AND Title <> ''
        """
    ).fetchone()
    return int(row["n"] if hasattr(row, "keys") else row[0])


def fill_title_zh_for_news_ids(
    connection: Any,
    news_ids: Sequence[int],
    translate: Callable[..., list[str]] | None = None,
) -> int:
    """按 NewsID 立即补 title_zh；已有中文的行跳过。失败由调用方处理。"""
    ids: list[int] = []
    seen: set[int] = set()
    for raw in news_ids:
        nid = int(raw)
        if nid in seen:
            continue
        seen.add(nid)
        ids.append(nid)
    if not ids:
        return 0
    translate_fn = translate or translate_texts
    done = 0
    for chunk in chunked(ids, MAX_BATCH_ITEMS):
        placeholders = ",".join("?" * len(chunk))
        rows = connection.execute(
            f"""
            SELECT id, Title
            FROM news
            WHERE NewsID IN ({placeholders})
              AND title_zh IS NULL
              AND Title IS NOT NULL
              AND Title <> ''
            """,
            chunk,
        ).fetchall()
        pending: list[tuple[int, str]] = []
        for row in rows:
            title = str(row["Title"] or "").strip()
            if title:
                pending.append((int(row["id"]), title[:MAX_ITEM_CHARS]))
        if not pending:
            continue
        pk_ids = [item[0] for item in pending]
        texts = [item[1] for item in pending]
        translated = translate_fn(texts, source="en", target="zh")
        for pk, title_zh in zip(pk_ids, translated, strict=True):
            connection.execute(
                "UPDATE news SET title_zh = ? WHERE id = ? AND title_zh IS NULL",
                (title_zh, pk),
            )
        connection.commit()
        done += len(pending)
    return done


def _run_fill_safe(news_ids: list[int]) -> None:
    try:
        settings = get_settings()
        with closing(connect(settings.database_file)) as connection:
            init_db(connection)
            done = fill_title_zh_for_news_ids(connection, news_ids)
        if done:
            logger.info("translate_tmt: immediate translated={}", done)
    except Exception:
        logger.exception("translate_tmt: immediate fill failed")


def schedule_fill_title_zh(news_ids: Sequence[int]) -> None:
    """入库后异步翻译，不阻塞采集线程。扫描 worker 仍作漏翻兜底。"""
    ids = [int(item) for item in news_ids]
    if not ids:
        return
    threading.Thread(
        target=_run_fill_safe,
        args=(ids,),
        daemon=True,
        name="title-zh-now",
    ).start()


def process_once(connection) -> int:
    settings = get_settings()
    secret_id = (settings.tmt_secret_id or "").strip()
    secret_key = (settings.tmt_secret_key or "").strip()
    if not secret_id or not secret_key:
        raise RuntimeError("FJ_TMT_SECRET_ID / FJ_TMT_SECRET_KEY 未配置")

    batch = _fetch_pending(connection, BATCH_SIZE)
    if not batch:
        return 0

    ids = [item[0] for item in batch]
    texts = [item[1] for item in batch]
    translated = translate_texts(texts, source="en", target="zh")
    for news_id, title_zh in zip(ids, translated, strict=True):
        connection.execute(
            "UPDATE news SET title_zh = ? WHERE id = ? AND title_zh IS NULL",
            (title_zh, news_id),
        )
    connection.commit()
    return len(batch)


def main() -> None:
    settings = get_settings()
    logger.info("translate_tmt: region={}", settings.tmt_region)
    while True:
        try:
            with closing(connect(settings.database_file)) as connection:
                init_db(connection)
                done = process_once(connection)
                left = _pending_count(connection)
            if done == 0:
                logger.info("translate_tmt: caught up, sleep {}s", IDLE_SLEEP_SECONDS)
                time.sleep(IDLE_SLEEP_SECONDS)
                continue
            logger.info("translate_tmt: translated={} remaining={}", done, left)
            time.sleep(0.3)
        except KeyboardInterrupt:
            logger.info("translate_tmt: stopped")
            return
        except Exception:
            logger.exception("translate_tmt: batch failed, retry in {}s", ERROR_SLEEP_SECONDS)
            time.sleep(ERROR_SLEEP_SECONDS)


if __name__ == "__main__":
    main()
