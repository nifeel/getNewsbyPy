from __future__ import annotations

import asyncio
import html
import json
import re
from typing import Any

from app.charts_db import ChartRow
from app.ingest.http_client import build_client, fetch_content

GITHUB_TRENDING_URL = "https://github.com/trending?since=daily"
APPSTORE_REGIONS = ("us", "gb", "de", "fr", "cn", "jp", "kr", "tw", "hk")
APPSTORE_CHARTS = {
    "free": "topfreeapplications",
    "paid": "toppaidapplications",
    "grossing": "topgrossingapplications",
}
APPSTORE_LIMIT = 50
APPSTORE_CONCURRENCY = 4

_ROW_SPLIT = '<article class="Box-row'
_TAG = re.compile(r"<[^>]+>")
_REPO = re.compile(r'<h2[^>]*>\s*<a[^>]*href="/([^"/]+/[^"/]+)"', re.S)
_DESC = re.compile(r'<p class="col-9[^"]*">(.*?)</p>', re.S)
_LANG = re.compile(r'itemprop="programmingLanguage">([^<]+)<')
_TODAY = re.compile(r"([\d,]+)\s+stars\s+today")


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAG.sub("", fragment)).split())


def _int(value: str) -> int | None:
    digits = value.replace(",", "").strip()
    return int(digits) if digits.isdigit() else None


def parse_github_trending(page: str) -> list[ChartRow]:
    rows: list[ChartRow] = []
    for block in page.split(_ROW_SPLIT)[1:]:
        repo = _REPO.search(block)
        if not repo:
            continue
        full_name = repo.group(1)
        desc = _DESC.search(block)
        lang = _LANG.search(block)
        total = re.search(rf'href="/{re.escape(full_name)}/stargazers"[^>]*>.*?</svg>\s*([\d,]+)', block, re.S)
        today = _TODAY.search(block)
        rows.append(
            ChartRow(
                rank=len(rows) + 1,
                item_id=full_name,
                title=full_name,
                subtitle=_text(desc.group(1)) if desc else "",
                url=f"https://github.com/{full_name}",
                category=lang.group(1).strip() if lang else "",
                stars_today=_int(today.group(1)) if today else None,
                stars_total=_int(total.group(1)) if total else None,
            )
        )
    return rows


def _label(node: Any) -> str:
    if isinstance(node, dict):
        return str(node.get("label") or "").strip()
    return ""


def parse_appstore_feed(payload: Any) -> list[ChartRow]:
    feed = payload.get("feed") if isinstance(payload, dict) else None
    entries = feed.get("entry") if isinstance(feed, dict) else None
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return []
    rows: list[ChartRow] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        title = _label(entry.get("im:name"))
        id_node = entry.get("id") if isinstance(entry.get("id"), dict) else {}
        app_id = str((id_node.get("attributes") or {}).get("im:id") or "").strip()
        if not title or not app_id:
            continue
        images = entry.get("im:image") if isinstance(entry.get("im:image"), list) else []
        category = entry.get("category") if isinstance(entry.get("category"), dict) else {}
        rows.append(
            ChartRow(
                rank=len(rows) + 1,
                item_id=app_id,
                title=title,
                subtitle=_label(entry.get("im:artist")),
                url=_label(id_node),
                category=str((category.get("attributes") or {}).get("label") or "").strip(),
                icon_url=_label(images[-1]) if images else "",
                price=_label(entry.get("im:price")),
            )
        )
    return rows


def appstore_feed_url(region: str, chart: str, limit: int = APPSTORE_LIMIT) -> str:
    return f"https://itunes.apple.com/{region}/rss/{APPSTORE_CHARTS[chart]}/limit={limit}/json"


async def fetch_github_trending() -> list[ChartRow]:
    async with build_client(via_proxy=True) as client:
        response = await client.get(GITHUB_TRENDING_URL, headers={"Accept": "text/html"})
        response.raise_for_status()
    rows = parse_github_trending(response.text)
    if not rows:
        raise RuntimeError("GitHub 热榜页面没有解析出仓库")
    return rows


async def fetch_appstore_charts() -> tuple[dict[tuple[str, str], list[ChartRow]], list[str]]:
    gate = asyncio.Semaphore(APPSTORE_CONCURRENCY)

    async def one(region: str, chart: str) -> tuple[str, str, list[ChartRow] | None, str]:
        async with gate:
            try:
                content, _via = await fetch_content(appstore_feed_url(region, chart))
                rows = parse_appstore_feed(json.loads(content.decode("utf-8")))
            except Exception as exc:  # noqa: BLE001 - 单个区失败不影响其他区
                return region, chart, None, f"{region}-{chart}: {type(exc).__name__}"
        if not rows:
            return region, chart, None, f"{region}-{chart}: 空榜"
        return region, chart, rows, ""

    results = await asyncio.gather(
        *(one(region, chart) for region in APPSTORE_REGIONS for chart in APPSTORE_CHARTS)
    )
    charts: dict[tuple[str, str], list[ChartRow]] = {}
    errors: list[str] = []
    for region, chart, rows, err in results:
        if rows is None:
            errors.append(err)
        else:
            charts[(region, chart)] = rows
    return charts, errors
