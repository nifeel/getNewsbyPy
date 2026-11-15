from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint("url_hash", name="uq_articles_url_hash"),
        UniqueConstraint("source_id", "title_hash", name="uq_articles_source_title"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(512))
    url: Mapped[str] = mapped_column(String(1024))
    summary: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    url_hash: Mapped[str] = mapped_column(String(64), index=True)
    title_hash: Mapped[str] = mapped_column(String(64))
    lang: Mapped[str] = mapped_column(String(8), default="zh")
    kind: Mapped[str] = mapped_column(String(16), default="news")
    title_zh: Mapped[str] = mapped_column(String(512), default="")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(512))
    start_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    location: Mapped[str] = mapped_column(String(256), default="")
    url: Mapped[str] = mapped_column(String(1024), default="")
    status: Mapped[str] = mapped_column(String(32), default="estimated")
    category: Mapped[str] = mapped_column(String(32), default="conference")
    note: Mapped[str] = mapped_column(Text, default="")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    source: Mapped[str] = mapped_column(String(64), default="seed_cn")
    time: Mapped[str] = mapped_column(String(16), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SourceState(Base):
    __tablename__ = "source_states"

    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(32), default="idle")
    last_error: Mapped[str] = mapped_column(Text, default="")
    last_count: Mapped[int] = mapped_column(Integer, default=0)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TitleCache(Base):
    """FJ 等直读源的中文标题缓存。禁止写回 FinancialJuice。"""

    __tablename__ = "title_cache"

    cache_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_hash: Mapped[str] = mapped_column(String(32), default="")
    title_zh: Mapped[str] = mapped_column(String(512), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HotItem(Base):
    __tablename__ = "hot_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(512))
    url: Mapped[str] = mapped_column(String(1024), default="")
    extra: Mapped[str] = mapped_column(String(64), default="")
    batch_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
