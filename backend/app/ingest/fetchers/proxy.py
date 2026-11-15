from app.ingest.fetchers.base import BaseFetcher
from app.ingest.fetchers.rss import parse_rss
from app.ingest.http_client import fetch_content
from app.ingest.sources import SourceConfig
from app.ingest.types import ProxyRequiredError, RawItem
from app.proxy_settings import get_effective_proxy


class ProxyFetcher(BaseFetcher):
    """国外源只走本机代理，禁止直连。"""

    access = "proxy"

    async def fetch(self, source: SourceConfig) -> list[RawItem]:
        if not get_effective_proxy():
            raise ProxyRequiredError(source.id)
        content, _via = await fetch_content(source.fetch_url, prefer_proxy=True)
        return parse_rss(content)
