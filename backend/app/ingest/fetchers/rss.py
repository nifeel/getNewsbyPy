import feedparser

from app.ingest.fetchers.base import BaseFetcher
from app.ingest.http_client import fetch_content
from app.ingest.normalize import parse_feed_datetime
from app.ingest.sources import SourceConfig
from app.ingest.types import RawItem


def parse_rss(content: bytes) -> list[RawItem]:
    parsed = feedparser.parse(content)
    items: list[RawItem] = []
    for entry in parsed.entries:
        title = str(entry.get("title") or "").strip()
        url = str(entry.get("link") or "").strip()
        if not title or not url:
            continue
        summary = str(entry.get("summary") or entry.get("description") or "").strip()
        published = (
            entry.get("published_parsed")
            or entry.get("updated_parsed")
            or entry.get("published")
            or entry.get("updated")
        )
        items.append(
            RawItem(
                title=title,
                url=url,
                summary=summary,
                published_at=parse_feed_datetime(published),
            )
        )
    return items


class RssFetcher(BaseFetcher):
    access = "direct"

    async def fetch(self, source: SourceConfig) -> list[RawItem]:
        content, _via = await fetch_content(source.fetch_url, prefer_proxy=False)
        return parse_rss(content)


class RssHubFetcher(RssFetcher):
    access = "rsshub"
