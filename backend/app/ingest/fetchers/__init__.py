from app.ingest.fetchers.base import BaseFetcher
from app.ingest.fetchers.financialjuice import FinancialJuiceFetcher
from app.ingest.fetchers.proxy import ProxyFetcher
from app.ingest.fetchers.rss import RssFetcher, RssHubFetcher
from app.ingest.sources import SourceConfig

_FETCHERS: dict[str, BaseFetcher] = {
    "direct": RssFetcher(),
    "rsshub": RssHubFetcher(),
    "proxy": ProxyFetcher(),
    "fj_readonly": FinancialJuiceFetcher(),
}


def get_fetcher(source: SourceConfig) -> BaseFetcher:
    try:
        return _FETCHERS[source.access]
    except KeyError as exc:
        raise ValueError(f"unknown access mode: {source.access}") from exc
