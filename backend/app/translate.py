from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Article, TitleCache
from app.schemas import ArticleOut, EventOut
from app.timeutil import utc_now
from app.translate_settings import get_effective_translate_url

logger = logging.getLogger(__name__)

BATCH_SIZE = 40
MAX_ITEM_CHARS = 512
FJ_SOURCE_ID = "financialjuice"


def has_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def needs_translate(title: str, lang: str = "") -> bool:
    text = title.strip()
    if not text or has_cjk(text):
        return False
    if lang == "zh":
        return False
    return True


def source_hash(title: str) -> str:
    return hashlib.sha256(title.strip().encode("utf-8")).hexdigest()[:16]


def cache_key(source_id: str, item_id: int) -> str:
    return f"{source_id}:{item_id}"


def event_cache_key(event_id: str) -> str:
    return f"event:{event_id}"


def parse_translate_payload(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        raise RuntimeError("翻译接口返回不是对象")
    code = payload.get("code")
    if code not in (0, 200):
        raise RuntimeError(str(payload.get("message") or "翻译失败"))
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("翻译接口缺少 data")
    items = data.get("items")
    if not isinstance(items, list):
        raise RuntimeError("翻译接口缺少 items")
    out: list[str] = []
    for row in items:
        if not isinstance(row, dict):
            raise RuntimeError("翻译条目格式错误")
        text = str(row.get("translated") or "").strip()
        if not text:
            raise RuntimeError("翻译结果为空")
        out.append(text[:MAX_ITEM_CHARS])
    return out


def translate_texts(
    texts: list[str],
    *,
    source: str = "en",
    target: str = "zh",
    post: Callable[..., object] | None = None,
) -> list[str]:
    cleaned = [item.strip()[:MAX_ITEM_CHARS] for item in texts]
    if not cleaned:
        return []
    base = get_effective_translate_url().rstrip("/")
    if not base:
        raise RuntimeError("翻译服务未启用")
    url = base + "/translate"

    def _post(target_url: str, payload: dict) -> object:
        with httpx.Client(timeout=20.0, trust_env=False) as client:
            response = client.post(target_url, json=payload)
            response.raise_for_status()
            return response.json()

    sender = post or _post
    payload = sender(url, {"q": cleaned, "source": source, "target": target})
    translated = parse_translate_payload(payload)
    if len(translated) != len(cleaned):
        raise RuntimeError("翻译条数与请求不一致")
    return translated


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _save_zh(
    session: Session,
    item: ArticleOut,
    original: str,
    zh_text: str,
    local_rows: dict[int, Article],
    cached: dict[str, TitleCache],
) -> None:
    now = utc_now()
    digest = source_hash(original)
    key = cache_key(item.source_id, item.id)
    if item.source_id != FJ_SOURCE_ID:
        row = local_rows.get(item.id)
        if row is not None:
            row.title_zh = zh_text
    hit = cached.get(key)
    if hit is None:
        hit = TitleCache(cache_key=key, source_hash=digest, title_zh=zh_text, updated_at=now)
        session.add(hit)
        cached[key] = hit
    else:
        hit.source_hash = digest
        hit.title_zh = zh_text
        hit.updated_at = now


def apply_title_zh(
    session: Session,
    items: list[ArticleOut],
    *,
    translate: Callable[..., list[str]] | None = None,
) -> list[ArticleOut]:
    pending = [item for item in items if needs_translate(item.title, item.lang)]
    if not pending:
        return items

    keys = [cache_key(item.source_id, item.id) for item in pending]
    cached = {
        row.cache_key: row
        for row in session.scalars(select(TitleCache).where(TitleCache.cache_key.in_(keys))).all()
    }
    local_ids = [item.id for item in pending if item.source_id != FJ_SOURCE_ID]
    local_rows = (
        {
            row.id: row
            for row in session.scalars(select(Article).where(Article.id.in_(local_ids))).all()
        }
        if local_ids
        else {}
    )

    missing: list[tuple[ArticleOut, str]] = []
    for item in pending:
        original = item.title
        digest = source_hash(original)
        key = cache_key(item.source_id, item.id)
        if item.source_id != FJ_SOURCE_ID:
            row = local_rows.get(item.id)
            if row and row.title_zh.strip():
                item.title = row.title_zh.strip()
                continue
        hit = cached.get(key)
        if hit and hit.source_hash == digest and hit.title_zh.strip():
            item.title = hit.title_zh.strip()
            continue
        missing.append((item, original))

    if not missing:
        session.commit()
        return items

    if translate is None and not get_effective_translate_url():
        return items

    translate_fn = translate or translate_texts
    originals = [title for _item, title in missing]
    try:
        translated: list[str] = []
        for chunk in _chunks(originals, BATCH_SIZE):
            translated.extend(translate_fn(chunk, source="en", target="zh"))
        for (item, original), zh in zip(missing, translated, strict=True):
            zh_text = zh.strip()[:MAX_ITEM_CHARS]
            if not zh_text:
                continue
            item.title = zh_text
            _save_zh(session, item, original, zh_text, local_rows, cached)
        session.commit()
    except Exception:
        logger.exception("translate titles failed")
        session.rollback()
    return items


def fill_new_article_titles(session: Session, article_ids: list[int]) -> int:
    if not article_ids:
        return 0
    rows = session.scalars(select(Article).where(Article.id.in_(article_ids))).all()
    items = [
        ArticleOut(
            id=row.id,
            source_id=row.source_id,
            source_name=row.source_id,
            title=row.title,
            url=row.url,
            summary="",
            published_at=row.published_at,
            fetched_at=row.fetched_at,
            lang=row.lang,
            kind=row.kind,
        )
        for row in rows
        if needs_translate(row.title, row.lang)
    ]
    before = [item.title for item in items]
    apply_title_zh(session, items)
    return sum(1 for item, old in zip(items, before, strict=True) if item.title != old)


def apply_event_title_zh(
    session: Session,
    items: list[EventOut],
    *,
    translate: Callable[..., list[str]] | None = None,
) -> list[EventOut]:
    pending = [item for item in items if needs_translate(item.title)]
    if not pending:
        return items

    keys = [event_cache_key(item.id) for item in pending]
    cached = {
        row.cache_key: row
        for row in session.scalars(select(TitleCache).where(TitleCache.cache_key.in_(keys))).all()
    }

    missing: list[tuple[EventOut, str, str]] = []
    for item in pending:
        original = item.title
        digest = source_hash(original)
        key = event_cache_key(item.id)
        hit = cached.get(key)
        if hit and hit.source_hash == digest and hit.title_zh.strip():
            item.title = hit.title_zh.strip()
            continue
        missing.append((item, original, key))

    if not missing:
        session.commit()
        return items

    if translate is None and not get_effective_translate_url():
        return items

    translate_fn = translate or translate_texts
    originals = [title for _item, title, _key in missing]
    try:
        translated: list[str] = []
        for chunk in _chunks(originals, BATCH_SIZE):
            translated.extend(translate_fn(chunk, source="en", target="zh"))
        now = utc_now()
        for (item, original, key), zh in zip(missing, translated, strict=True):
            zh_text = zh.strip()[:MAX_ITEM_CHARS]
            if not zh_text:
                continue
            item.title = zh_text
            digest = source_hash(original)
            hit = cached.get(key)
            if hit is None:
                hit = TitleCache(cache_key=key, source_hash=digest, title_zh=zh_text, updated_at=now)
                session.add(hit)
                cached[key] = hit
            else:
                hit.source_hash = digest
                hit.title_zh = zh_text
                hit.updated_at = now
        session.commit()
    except Exception:
        logger.exception("translate event titles failed")
        session.rollback()
    return items
