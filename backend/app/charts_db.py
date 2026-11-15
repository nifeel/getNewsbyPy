from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Engine,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    func,
    insert,
    select,
)

from app.config import settings

metadata = MetaData()

chart_items = Table(
    "chart_items",
    metadata,
    Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column("board", String(32), nullable=False),
    Column("region", String(16), nullable=False),
    Column("chart", String(16), nullable=False),
    # Asia/Shanghai 业务日；同一天重复采集会整体覆盖当天快照
    Column("snap_date", Date, nullable=False),
    Column("rank", Integer, nullable=False),
    Column("item_id", String(256), nullable=False),
    Column("title", String(512), nullable=False),
    Column("subtitle", Text, nullable=False, default=""),
    Column("url", String(1024), nullable=False, default=""),
    Column("category", String(128), nullable=False, default=""),
    Column("icon_url", String(1024), nullable=False, default=""),
    Column("price", String(32), nullable=False, default=""),
    Column("stars_today", Integer, nullable=True),
    Column("stars_total", Integer, nullable=True),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("board", "region", "chart", "snap_date", "rank", name="uq_chart_items_slot"),
    Index("ix_chart_items_lookup", "board", "region", "chart", "snap_date"),
)


@dataclass
class ChartRow:
    rank: int
    item_id: str
    title: str
    subtitle: str = ""
    url: str = ""
    category: str = ""
    icon_url: str = ""
    price: str = ""
    stars_today: int | None = None
    stars_total: int | None = None


def charts_configured() -> bool:
    return bool(settings.charts_database_url.strip())


@lru_cache(maxsize=1)
def get_charts_engine() -> Engine:
    if not charts_configured():
        raise RuntimeError("榜单数据库未配置")
    return create_engine(settings.charts_database_url, pool_pre_ping=True, pool_size=2, max_overflow=2)


def init_charts_db(engine: Engine | None = None) -> None:
    if engine is None:
        if not charts_configured():
            return
        engine = get_charts_engine()
    metadata.create_all(engine)


def replace_snapshot(
    engine: Engine,
    *,
    board: str,
    region: str,
    chart: str,
    snap_date: date,
    rows: list[ChartRow],
    fetched_at: datetime,
) -> int:
    slot = (
        (chart_items.c.board == board)
        & (chart_items.c.region == region)
        & (chart_items.c.chart == chart)
        & (chart_items.c.snap_date == snap_date)
    )
    with engine.begin() as conn:
        conn.execute(delete(chart_items).where(slot))
        if rows:
            conn.execute(
                insert(chart_items),
                [
                    {
                        "board": board,
                        "region": region,
                        "chart": chart,
                        "snap_date": snap_date,
                        "rank": row.rank,
                        "item_id": row.item_id[:256],
                        "title": row.title[:512],
                        "subtitle": row.subtitle,
                        "url": row.url[:1024],
                        "category": row.category[:128],
                        "icon_url": row.icon_url[:1024],
                        "price": row.price[:32],
                        "stars_today": row.stars_today,
                        "stars_total": row.stars_total,
                        "fetched_at": fetched_at,
                    }
                    for row in rows
                ],
            )
    return len(rows)


def list_snap_dates(engine: Engine, *, board: str, region: str, chart: str, limit: int = 60) -> list[date]:
    stmt = (
        select(chart_items.c.snap_date)
        .where(
            chart_items.c.board == board,
            chart_items.c.region == region,
            chart_items.c.chart == chart,
        )
        .group_by(chart_items.c.snap_date)
        .order_by(chart_items.c.snap_date.desc())
        .limit(limit)
    )
    with engine.connect() as conn:
        return [row[0] for row in conn.execute(stmt)]


def read_snapshot(
    engine: Engine, *, board: str, region: str, chart: str, day: date | None
) -> tuple[date | None, datetime | None, list[dict]]:
    base = (
        (chart_items.c.board == board)
        & (chart_items.c.region == region)
        & (chart_items.c.chart == chart)
    )
    with engine.connect() as conn:
        pick = select(func.max(chart_items.c.snap_date)).where(base)
        if day is not None:
            pick = pick.where(chart_items.c.snap_date <= day)
        snap_date = conn.scalar(pick)
        if snap_date is None:
            return None, None, []
        rows = conn.execute(
            select(chart_items)
            .where(base, chart_items.c.snap_date == snap_date)
            .order_by(chart_items.c.rank.asc())
        ).mappings().all()
    items = [dict(row) for row in rows]
    fetched_at = max((row["fetched_at"] for row in items), default=None)
    return snap_date, fetched_at, items
