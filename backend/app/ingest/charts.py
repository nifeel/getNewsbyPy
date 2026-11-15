from app.charts_db import get_charts_engine, replace_snapshot
from app.ingest.fetchers.charts import fetch_appstore_charts, fetch_github_trending
from app.ingest.sources import SourceConfig
from app.timeutil import today_biz, utc_now

GITHUB_SOURCE = "github-trending"
APPSTORE_SOURCE = "appstore-top"


async def sync_github_trending() -> tuple[int, str]:
    rows = await fetch_github_trending()
    count = replace_snapshot(
        get_charts_engine(),
        board="github",
        region="global",
        chart="daily",
        snap_date=today_biz(),
        rows=rows,
        fetched_at=utc_now(),
    )
    return count, ""


async def sync_appstore_top() -> tuple[int, str]:
    charts, errors = await fetch_appstore_charts()
    if not charts:
        raise RuntimeError("App Store 榜单全部失败: " + "; ".join(errors[:5]))
    engine = get_charts_engine()
    day = today_biz()
    now = utc_now()
    count = 0
    for (region, chart), rows in charts.items():
        count += replace_snapshot(
            engine,
            board="appstore",
            region=region,
            chart=chart,
            snap_date=day,
            rows=rows,
            fetched_at=now,
        )
    note = f"部分失败 {len(errors)} 个: " + "; ".join(errors) if errors else ""
    return count, note


async def sync_chart_source(source: SourceConfig) -> tuple[int, str]:
    if source.id == GITHUB_SOURCE:
        return await sync_github_trending()
    if source.id == APPSTORE_SOURCE:
        return await sync_appstore_top()
    raise RuntimeError(f"未知榜单来源: {source.id}")
