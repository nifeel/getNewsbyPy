from app.config import settings
from app.fj_settings import get_effective_fj_url
from app.ingest.normalize import canonical_url, title_hash
from app.ingest.sources import load_sources


def test_default_http_proxy_is_local_clash() -> None:
    assert settings.http_proxy == "http://127.0.0.1:7897"


def test_canonical_url_strips_tracking() -> None:
    url = "https://WWW.Example.com/a/b/?utm_source=rss&id=1#hash"
    assert canonical_url(url) == "https://www.example.com/a/b?id=1"


def test_title_hash_is_stable() -> None:
    left = title_hash("36kr", "  OpenAI  发布  ")
    right = title_hash("36kr", "OpenAI 发布")
    assert left == right


def test_cn_direct_sources_are_runnable() -> None:
    sources = load_sources()
    cn_direct = [
        item for item in sources if item.region == "cn" and item.access == "direct" and item.enabled
    ]
    assert cn_direct
    assert all(item.runnable for item in cn_direct)


def test_intl_proxy_sources_are_runnable_when_proxy_configured() -> None:
    sources = load_sources()
    proxy_items = [
        item for item in sources if item.region == "intl" and item.enabled and item.access == "proxy"
    ]
    assert proxy_items
    assert all(item.runnable for item in proxy_items)


def test_financialjuice_source_requires_readonly_url() -> None:
    sources = {item.id: item for item in load_sources()}
    item = sources["financialjuice"]
    assert item.access == "fj_readonly"
    assert item.runnable == bool(get_effective_fj_url())
