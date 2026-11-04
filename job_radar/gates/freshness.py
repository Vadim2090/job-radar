"""Posting age, the one definition every command uses.

Age = now minus the posting's published time. A full timestamp counts as is (no offset = UTC). A date without a
time counts from 00:00 of that date in the configured time zone. A missing or unparseable published date is NOT
fresh. A published time in the future is fresh only up to FUTURE_SKEW_HOURS ahead (clock skew); further out it is
treated as bogus.
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

FRESH_HOURS = 24
FUTURE_SKEW_HOURS = 1


def published_at(posted, tz="UTC"):
    """Published time as an aware datetime, or None when missing or unparseable."""
    s = (posted or "").strip() if isinstance(posted, str) else ""
    if not s:
        return None
    try:
        if len(s) == 10:
            return datetime.combine(date.fromisoformat(s), time(), ZoneInfo(tz))
        start = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return start if start.tzinfo else start.replace(tzinfo=timezone.utc)


def age_hours(posted, now=None, tz="UTC"):
    """Hours since publication (negative when in the future), or None when the date is missing or unparseable."""
    start = published_at(posted, tz)
    if start is None:
        return None
    return ((now or datetime.now(timezone.utc)) - start).total_seconds() / 3600


def is_fresh(posted, now=None, hours=FRESH_HOURS, tz="UTC"):
    """True when the posting was published within the last `hours` hours."""
    age = age_hours(posted, now, tz)
    return age is not None and -FUTURE_SKEW_HOURS <= age <= hours


def window_start(now=None, hours=FRESH_HOURS):
    """Earliest published time that still counts as fresh."""
    return (now or datetime.now(timezone.utc)) - timedelta(hours=hours)
