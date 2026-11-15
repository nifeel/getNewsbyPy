from datetime import datetime
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from app.collectors.startup_api_client import eastern_time_offset_hours, normalize_news_url, startup_url_with_old_id


def query_value(url: str, key: str) -> str:
    values = parse_qs(urlparse(url).query, keep_blank_values=True)[key]
    return values[0]


def test_eastern_time_offset_handles_dst() -> None:
    assert eastern_time_offset_hours(datetime(2026, 7, 1, tzinfo=ZoneInfo("America/New_York"))) == -4
    assert eastern_time_offset_hours(datetime(2026, 1, 1, tzinfo=ZoneInfo("America/New_York"))) == -5


def test_normalize_news_url_overrides_captured_time_offset() -> None:
    url = "https://live.financialjuice.com/FJService.asmx/Startup?TimeOffset=8&oldID=0"

    normalized = normalize_news_url(url, offset_hours=-4)

    assert query_value(normalized, "TimeOffset") == "-4"
    assert query_value(normalized, "oldID") == "0"


def test_startup_url_with_old_id_keeps_eastern_time_offset() -> None:
    url = "https://live.financialjuice.com/FJService.asmx/GetPreviousNews?TimeOffset=8&oldID=0"

    updated = startup_url_with_old_id(url, 9590000)

    assert query_value(updated, "TimeOffset") == str(eastern_time_offset_hours())
    assert query_value(updated, "oldID") == "9590000"
