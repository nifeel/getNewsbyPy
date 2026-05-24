import asyncio
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from loguru import logger


NEWS_ENDPOINTS = ("Startup", "GetPreviousNews")
NEWS_ENDPOINT_MARKERS = tuple(f"FJService.asmx/{endpoint}" for endpoint in NEWS_ENDPOINTS)
NEWS_URL_PATTERN = re.compile(r"https://\S*?FJService\.asmx/(?:Startup|GetPreviousNews)\?\S+")
LOGIN_RESPONSE_PATTERN = re.compile(r"(login|sign in|signin|password)", re.IGNORECASE)
NEWS_TIME_ZONE = ZoneInfo("America/New_York")


class StartupApiError(RuntimeError):
    pass


class LoginRequiredError(StartupApiError):
    pass


def _cookie_domain_matches(host: str, cookie_domain: str) -> bool:
    normalized = cookie_domain.lstrip(".").lower()
    host = host.lower()
    return host == normalized or host.endswith(f".{normalized}")


def load_cookie_header(storage_state_file: Path, url: str) -> str:
    try:
        storage_state = json.loads(storage_state_file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StartupApiError(f"Login state not found: {storage_state_file}") from exc
    except json.JSONDecodeError as exc:
        raise StartupApiError(f"Login state is not valid JSON: {storage_state_file}") from exc

    host = urlparse(url).hostname or ""
    cookie_parts: list[str] = []
    for cookie in storage_state.get("cookies", []):
        if not isinstance(cookie, dict):
            continue
        name = cookie.get("name")
        value = cookie.get("value")
        domain = cookie.get("domain")
        if not name or value is None or not domain:
            continue
        if _cookie_domain_matches(host, str(domain)):
            cookie_parts.append(f"{name}={value}")

    return "; ".join(cookie_parts)


def read_cached_startup_url(cache_file: Path) -> str | None:
    if not cache_file.exists():
        return None
    cached_url = cache_file.read_text(encoding="utf-8").strip()
    if not any(marker in cached_url for marker in NEWS_ENDPOINT_MARKERS):
        return None
    return cached_url


def write_cached_startup_url(cache_file: Path, url: str) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(url, encoding="utf-8")


def find_latest_startup_url(log_file: Path) -> str | None:
    if not log_file.exists():
        return None

    latest_url: str | None = None
    for line in log_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = NEWS_URL_PATTERN.search(line)
        if match:
            latest_url = match.group(0).rstrip('",')
    return latest_url


def resolve_startup_url(configured_url: str | None, cache_file: Path, network_log_file: Path) -> str:
    if configured_url:
        return normalize_news_url(configured_url)

    cached_url = read_cached_startup_url(cache_file)
    if cached_url:
        logger.info("Using cached Startup API URL from {}", cache_file)
        return normalize_news_url(cached_url)

    discovered_url = find_latest_startup_url(network_log_file)
    if discovered_url:
        logger.info("Using Startup API URL discovered from {}", network_log_file)
        return normalize_news_url(discovered_url)

    raise StartupApiError(
        "News API URL is not configured and no captured Startup/GetPreviousNews URL was found. "
        "Set FJ_STARTUP_API_URL or run `python -m app.browser.network_sniffer` once."
    )


def eastern_time_offset_hours(moment: datetime | None = None) -> int:
    current = moment or datetime.now(NEWS_TIME_ZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=NEWS_TIME_ZONE)
    else:
        current = current.astimezone(NEWS_TIME_ZONE)
    offset = current.utcoffset()
    if offset is None:
        return -5
    return int(offset.total_seconds() // 3600)


def normalize_news_url(url: str, *, offset_hours: int | None = None) -> str:
    parsed = urlparse(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    next_query: list[tuple[str, str]] = []
    replaced = False
    time_offset = str(eastern_time_offset_hours() if offset_hours is None else offset_hours)
    for key, value in query:
        if key == "TimeOffset":
            next_query.append((key, time_offset))
            replaced = True
        else:
            next_query.append((key, value))
    if not replaced:
        next_query.append(("TimeOffset", time_offset))
    return urlunparse(parsed._replace(query=urlencode(next_query)))


def startup_url_with_old_id(url: str, old_id: int) -> str:
    parsed = urlparse(normalize_news_url(url))
    query = parse_qsl(parsed.query, keep_blank_values=True)
    replaced = False
    next_query: list[tuple[str, str]] = []
    for key, value in query:
        if key == "oldID":
            next_query.append((key, str(old_id)))
            replaced = True
        else:
            next_query.append((key, value))
    if not replaced:
        next_query.append(("oldID", str(old_id)))
    return urlunparse(parsed._replace(query=urlencode(next_query)))


def fetch_startup_payload_sync(url: str, storage_state_file: Path, timeout: int = 30) -> dict[str, Any]:
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": "https://www.financialjuice.com/home",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
        "X-Requested-With": "XMLHttpRequest",
    }
    cookie_header = load_cookie_header(storage_state_file, url)
    if cookie_header:
        headers["Cookie"] = cookie_header

    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            status = response.status
            body = response.read()
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise LoginRequiredError(f"Login state is no longer accepted: HTTP {exc.code}") from exc
        raise StartupApiError(f"Startup API returned HTTP {exc.code}") from exc
    except URLError as exc:
        raise StartupApiError(f"Startup API request failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise StartupApiError("Startup API request timed out") from exc

    if status >= 400:
        if status in {401, 403}:
            raise LoginRequiredError(f"Login state is no longer accepted: HTTP {status}")
        raise StartupApiError(f"Startup API returned HTTP {status}")

    return parse_startup_response_text(body.decode("utf-8-sig", errors="replace"))


def parse_startup_response_text(response_text: str) -> dict[str, Any]:
    try:
        decoded = json.loads(response_text)
        if isinstance(decoded, dict):
            return decoded
    except json.JSONDecodeError:
        pass

    if "<html" in response_text.lower() and LOGIN_RESPONSE_PATTERN.search(response_text):
        raise LoginRequiredError("Startup API returned a login page instead of JSON.")

    try:
        root = ET.fromstring(response_text)
    except ET.ParseError as exc:
        preview = " ".join(response_text.split())[:160]
        raise StartupApiError(f"Startup API response is not valid JSON: {preview}") from exc

    wrapped_text = root.text or ""
    try:
        decoded = json.loads(wrapped_text)
    except json.JSONDecodeError as exc:
        preview = " ".join(wrapped_text.split())[:160]
        raise StartupApiError(f"Startup API XML wrapper does not contain valid JSON: {preview}") from exc

    if not isinstance(decoded, dict):
        raise StartupApiError("Startup API JSON payload is not an object")

    return decoded


async def fetch_startup_payload(url: str, storage_state_file: Path, timeout: int = 30) -> dict[str, Any]:
    return await asyncio.to_thread(fetch_startup_payload_sync, url, storage_state_file, timeout)


async def fetch_startup_payload_via_context(
    url: str,
    context: Any,
    timeout: int = 30,
) -> dict[str, Any]:
    """通过浏览器自身的 cookie jar 获取 JSON 数据。

    使用 Playwright 的 APIRequestContext，cookie 自动管理 —
    无需从 storage_state.json 中提取。
    """
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": "https://www.financialjuice.com/home",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
        "X-Requested-With": "XMLHttpRequest",
    }
    try:
        response = await context.request.get(url, headers=headers, timeout=timeout * 1000)
    except Exception as exc:
        raise StartupApiError(f"Browser request failed: {exc}") from exc

    if response.status in {401, 403}:
        raise LoginRequiredError(f"Login state is no longer accepted: HTTP {response.status}")
    if response.status >= 400:
        raise StartupApiError(f"Startup API returned HTTP {response.status}")

    text = await response.text()
    return parse_startup_response_text(text)
