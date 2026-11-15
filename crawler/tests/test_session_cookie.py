import json
from datetime import datetime, timezone
from pathlib import Path

from app.browser.session import earliest_auth_cookie_expiry, format_cookie_expires_at


def test_earliest_auth_cookie_expiry_uses_soonest_named_cookie(tmp_path: Path) -> None:
    path = tmp_path / "storage_state.json"
    path.write_text(
        json.dumps(
            {
                "cookies": [
                    {"name": "other", "expires": 1_900_000_000},
                    {"name": ".ASPXAUTH", "expires": 1_800_000_000},
                    {"name": "FJ-UID", "expires": 1_700_000_000},
                    {"name": "FJ-UName", "expires": -1},
                ]
            }
        ),
        encoding="utf-8",
    )
    got = earliest_auth_cookie_expiry(path)
    assert got == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)
    assert format_cookie_expires_at(got) == "2023-11-14T22:13:20Z"


def test_earliest_auth_cookie_expiry_missing_file() -> None:
    assert earliest_auth_cookie_expiry(Path("/tmp/no-such-storage-state.json")) is None
