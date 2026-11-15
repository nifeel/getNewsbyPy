from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ingest.fetchers.digest_feeds import econ_to_event, event_row_payload, parse_econ_calendar
from app.ingest.remote_events import _parse_techconf, upsert_events
from app.models import Base, Event
from app.timeutil import today_biz


def test_parse_techconf_keeps_upcoming_only() -> None:
    today = today_biz()
    past = date(2020, 1, 1).isoformat()
    future = date(today.year + 1, 6, 1).isoformat()
    rows = _parse_techconf(
        {
            "data": [
                {
                    "id": "old-conf",
                    "title": "Old",
                    "upcoming_events": {
                        "2020": {"dates": {"start": past, "end": past}, "location": {"city": "X", "country": "US"}}
                    },
                },
                {
                    "id": "pycon",
                    "title": "PyCon",
                    "upcoming_events": {
                        str(today.year + 1): {
                            "dates": {"start": future, "end": future},
                            "format": "in-person",
                            "location": {"city": "Berlin", "country": "DE"},
                        }
                    },
                },
            ]
        }
    )
    assert len(rows) == 1
    assert rows[0]["id"] == f"techconf-pycon-{today.year + 1}"
    assert rows[0]["location"] == "Berlin DE"


def test_upsert_econ_event_persists_time() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
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
    out = econ_to_event(items[0])
    upsert_events(session, [event_row_payload(out)])
    row = session.get(Event, out.id)
    assert row is not None
    assert row.time == "09:30"
    assert row.category == "econ"
    assert row.source == "forex-factory-calendar"
    session.close()
