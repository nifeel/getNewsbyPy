import logging

import httpx

from app.config import settings
from app.proxy_settings import get_effective_proxy

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": settings.user_agent,
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
}

_RETRYABLE = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)


def build_client(*, via_proxy: bool, timeout_seconds: float | None = None) -> httpx.AsyncClient:
    options: dict = {
        "timeout": httpx.Timeout(timeout_seconds or settings.http_timeout_seconds),
        "follow_redirects": True,
        "headers": _HEADERS,
        "trust_env": False,
        "limits": httpx.Limits(max_connections=1, max_keepalive_connections=0),
    }
    if via_proxy:
        proxy = get_effective_proxy()
        if not proxy:
            raise RuntimeError("代理未配置或未启用")
        options["proxy"] = proxy
    return httpx.AsyncClient(**options)


async def fetch_content(
    url: str,
    *,
    prefer_proxy: bool = False,
    timeout_seconds: float | None = None,
) -> tuple[bytes, str]:
    if prefer_proxy:
        async with build_client(via_proxy=True, timeout_seconds=timeout_seconds) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content, "proxy"

    try:
        async with build_client(via_proxy=False, timeout_seconds=timeout_seconds) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content, "direct"
    except _RETRYABLE as exc:
        proxy = get_effective_proxy()
        if not proxy:
            raise
        logger.warning("direct fetch failed (%s), retry via proxy: %s", type(exc).__name__, url)
        async with build_client(via_proxy=True, timeout_seconds=timeout_seconds) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content, "proxy"
