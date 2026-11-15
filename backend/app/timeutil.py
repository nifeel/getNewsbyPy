from datetime import date, datetime, timezone, tzinfo
from zoneinfo import ZoneInfo

BIZ_ZONE = ZoneInfo("Asia/Shanghai")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def assume_tz(value: datetime, zone: tzinfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=zone)
    return value


def today_biz() -> date:
    return datetime.now(BIZ_ZONE).date()


def to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()
