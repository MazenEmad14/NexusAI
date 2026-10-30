"""Time zones: every "today" or "tomorrow" is the user's own, never the server's.

Server clocks are not in the user's zone, and a naive local time sent to Google with a "Z" suffix is
simply wrong. Zones are IANA names ("Africa/Cairo") stored on the user's profile, with a deployment
default for users who have not set one.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from helpers.config import get_settings


def is_valid_timezone(name: Optional[str]) -> bool:
    if not name or not isinstance(name, str) or len(name) > 64:
        return False
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False


def resolve_timezone(name: Optional[str] = None) -> ZoneInfo:
    """The user's zone, else the deployment default, else UTC."""
    for candidate in (name, get_settings().DEFAULT_TIMEZONE):
        if is_valid_timezone(candidate):
            return ZoneInfo(candidate)
    return ZoneInfo("UTC")


def local_now(name: Optional[str] = None) -> datetime:
    """Current time as an aware datetime in the user's zone."""
    return datetime.now(timezone.utc).astimezone(resolve_timezone(name))


def day_bounds(name: Optional[str] = None, now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    """Start (inclusive) and end (exclusive) of the user's current day, as aware datetimes.

    The end is the next local midnight, so the day is 23 or 25 hours long when daylight saving changes.
    """
    zone = resolve_timezone(name)
    now = (now or datetime.now(timezone.utc)).astimezone(zone)
    start = datetime(now.year, now.month, now.day, tzinfo=zone)
    next_day = (start + timedelta(days=1))
    end = datetime(next_day.year, next_day.month, next_day.day, tzinfo=zone)
    return start, end
