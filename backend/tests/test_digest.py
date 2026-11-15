from datetime import date

from app.digest import lookahead_dates, week_bounds
from app.ingest.fetchers.digest_feeds import (
    econ_to_event,
    event_to_econ_item,
    parse_econ_calendar,
    parse_weibo_hot,
)
from app.ingest.sources import load_sources


def test_week_bounds_monday_to_sunday() -> None:
    monday, sunday, start, end = week_bounds(date(2026, 9, 3))
    assert monday == date(2026, 8, 31)
    assert sunday == date(2026, 9, 6)
    assert start.day == 31
    assert end.day == 7


def test_weekly_lookahead_is_next_week() -> None:
    start, end = lookahead_dates("weekly", date(2026, 9, 6))
    assert start == date(2026, 9, 7)
    assert end == date(2026, 9, 13)


def test_parse_econ_calendar_keeps_high_watchlist() -> None:
    items = parse_econ_calendar(
        [
            {
                "title": "GDP q/q",
                "country": "AUD",
                "date": "2026-09-01T21:30:00-04:00",
                "impact": "High",
                "forecast": "0.3%",
                "previous": "0.3%",
            },
            {
                "title": "Ignore low",
                "country": "USD",
                "date": "2026-09-01T10:00:00-04:00",
                "impact": "Low",
                "forecast": "",
                "previous": "",
            },
        ]
    )
    assert len(items) == 1
    assert items[0].country == "澳大利亚"
    assert items[0].biz_date == "2026-09-02"
    assert items[0].impact == "高"


def test_econ_to_event_maps_calendar_fields() -> None:
    items = parse_econ_calendar(
        [
            {
                "title": "GDP q/q",
                "country": "AUD",
                "date": "2026-09-01T21:30:00-04:00",
                "impact": "High",
                "forecast": "0.3%",
                "previous": "0.3%",
            }
        ]
    )
    event = econ_to_event(items[0])
    assert event.category == "econ"
    assert event.start_date == "2026-09-02"
    assert event.time == "09:30"
    assert event.location == "澳大利亚"
    assert event.source == "forex-factory-calendar"
    assert "影响高" in event.note
    assert "预期 0.3%" in event.note


def test_econ_event_roundtrip_keeps_forecast() -> None:
    items = parse_econ_calendar(
        [
            {
                "title": "GDP q/q",
                "country": "AUD",
                "date": "2026-09-01T21:30:00-04:00",
                "impact": "High",
                "forecast": "0.3%",
                "previous": "0.3%",
            }
        ]
    )
    back = event_to_econ_item(econ_to_event(items[0]))
    assert back.biz_date == items[0].biz_date
    assert back.impact == "高"
    assert back.forecast == "0.3%"
    assert back.previous == "0.3%"
    assert back.country == "澳大利亚"



def test_parse_weibo_hot_skips_ads() -> None:
    items = parse_weibo_hot(
        {
            "ok": 1,
            "data": {
                "realtime": [
                    {"note": "广告", "word": "广告", "is_ad": 1},
                    {"note": "正常热搜", "word": "正常热搜", "icon_desc": "热"},
                ]
            },
        }
    )
    assert [item.title for item in items] == ["正常热搜"]
    assert "q=" in items[0].url


def test_digest_aux_sources_are_listed() -> None:
    sources = {item.id: item for item in load_sources()}
    assert sources["forex-factory-calendar"].kind == "calendar"
    assert sources["weibo-hot"].kind == "hotspot"
